"""Evaluate a structural v2 candidate using development-only internal splits."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from a_share_quant.accounting.config import load_account_config
from a_share_quant.backtest.engine import StrategyBacktestEngine
from a_share_quant.data.storage import DataStore
from a_share_quant.execution.costs import load_cost_model
from a_share_quant.execution.rules import load_trading_rules
from a_share_quant.strategy.config import load_strategy_config
from a_share_quant.strategy.momentum import build_signals


class StructuralCandidatePipeline:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.store = DataStore(project_root)

    def run(self) -> dict[str, object]:
        daily = self.store.load_processed("etf_daily")
        actions = self.store.load_processed("etf_corporate_actions")
        calendar = self.store.load_processed("trading_calendar")
        benchmark = self.store.load_processed("benchmark_daily")
        universe = self.store.load_universe()
        base_config = load_strategy_config(self.project_root)
        account_config = load_account_config(self.project_root)
        cost_model = load_cost_model(self.project_root)
        rule_book = load_trading_rules(
            self.project_root,
            universe_symbols=set(universe["symbol"].astype(str).str.zfill(6)),
        )
        train_end = pd.Timestamp("2018-12-31")
        validation_start = pd.Timestamp("2019-01-01")
        validation_end = pd.Timestamp("2023-10-29")

        monthly = replace(
            base_config,
            rebalance_frequency="monthly",
            min_rebalance_turnover=0.10,
            min_trade_weight=0.04,
        )
        multi_horizon = replace(
            monthly,
            momentum_windows=(60, 120, 240),
            trend_windows=(60, 120, 200),
            trend_consensus_ratio=2 / 3,
        )
        full_v2 = replace(
            multi_horizon,
            market_filter=True,
            benchmark_trend_days=200,
            defensive_exposure=0.40,
            target_exposure=0.90,
            max_single_weight=0.40,
        )
        variants = [
            ("v1_weekly_single", base_config, False),
            ("monthly_only", monthly, False),
            ("monthly_multi_horizon", multi_horizon, False),
            ("v2_structural", full_v2, False),
            ("v2_structural_with_drawdown_control", full_v2, True),
        ]
        periods = [
            ("train", None, train_end),
            ("validation", validation_start, validation_end),
        ]

        summaries: list[dict[str, object]] = []
        snapshots_frames: list[pd.DataFrame] = []
        orders_frames: list[pd.DataFrame] = []
        for variant_name, config, drawdown_control in variants:
            bundle = build_signals(daily, calendar, config, benchmark=benchmark)
            for period_name, start, end in periods:
                result = StrategyBacktestEngine(
                    self.project_root,
                    strategy_config=config,
                    account_config=account_config,
                    drawdown_control=drawdown_control,
                ).run(
                    daily=daily,
                    actions=actions,
                    calendar=calendar,
                    benchmark=benchmark,
                    signal_bundle=bundle,
                    rule_book=rule_book,
                    cost_model=cost_model,
                    scenario=f"{variant_name}_{period_name}",
                    stress_multiplier=Decimal("1"),
                    with_costs=True,
                    start_date=start,
                    end_date=end,
                )
                summaries.append(
                    {
                        "variant": variant_name,
                        "period": period_name,
                        **result.summary,
                    }
                )
                snapshots_frames.append(result.snapshots)
                orders_frames.append(result.orders)

        summary = pd.DataFrame(summaries)
        snapshots = pd.concat(snapshots_frames, ignore_index=True)
        orders = pd.concat(orders_frames, ignore_index=True)
        v2 = summary[
            (summary["variant"] == "v2_structural")
            & (summary["period"] == "validation")
        ].iloc[0]
        v2_risk = summary[
            (summary["variant"] == "v2_structural_with_drawdown_control")
            & (summary["period"] == "validation")
        ].iloc[0]
        promotion_pass = bool(
            v2["excess_return"] > 0
            and v2["max_drawdown"] >= -0.20
            and v2["annualized_return"] > 0
        )
        errors: list[str] = []
        if not snapshots["reconciled"].all():
            errors.append("Structural candidate contains an unreconciled snapshot")
        if not orders.empty and (orders["execution_date"] <= orders["signal_date"]).any():
            errors.append("Structural candidate contains a non-forward order")
        payload = {
            "status": "ok" if not errors else "failed",
            "updated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "split": {
                "train_end": train_end.date(),
                "validation_start": validation_start.date(),
                "validation_end": validation_end.date(),
                "final_out_of_sample_used": False,
            },
            "v2_definition": {
                "rebalance_frequency": "monthly",
                "momentum_windows": [60, 120, 240],
                "trend_windows": [60, 120, 200],
                "trend_consensus_ratio": 2 / 3,
                "market_filter": "CSI300 above 200-day moving average",
                "defensive_exposure": 0.40,
                "target_exposure": 0.90,
                "max_single_weight": 0.40,
                "min_rebalance_turnover": 0.10,
            },
            "summary": summary.to_dict(orient="records"),
            "promotion_pass": promotion_pass,
            "v2_validation": {
                "total_return": float(v2["total_return"]),
                "annualized_return": float(v2["annualized_return"]),
                "max_drawdown": float(v2["max_drawdown"]),
                "excess_return": float(v2["excess_return"]),
                "annualized_one_way_turnover": float(v2["annualized_one_way_turnover"]),
            },
            "v2_risk_validation": {
                "total_return": float(v2_risk["total_return"]),
                "max_drawdown": float(v2_risk["max_drawdown"]),
                "excess_return": float(v2_risk["excess_return"]),
            },
            "errors": errors,
        }
        self.store.save_processed(summary, "structural_scenarios")
        self.store.save_processed(snapshots, "structural_snapshots")
        self.store.save_processed(orders, "structural_orders")
        self.store.update_structural_database(summary, snapshots, orders)
        self.store.save_structural_report(payload)
        self._write_report(
            self.project_root / "runs" / "structural_v2" / "report.md",
            summary,
            promotion_pass,
        )
        if errors:
            raise ValueError("Structural candidate validation failed: " + "; ".join(errors))
        return payload

    @staticmethod
    def _write_report(path: Path, summary: pd.DataFrame, promotion_pass: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        table = summary[
            [
                "variant",
                "period",
                "total_return",
                "annualized_return",
                "max_drawdown",
                "sharpe_ratio",
                "excess_return",
                "annualized_one_way_turnover",
            ]
        ]
        content = f"""# 第二版结构性候选

## 设计约束

- 只使用2012-2018训练期和2019-2023内部验证期。
- 最近2023-10-30以后的最终样本外没有参与本版参数或结构选择。
- 第二版采用月度调仓、60/120/240日动量共识、60/120/200日趋势共识，以及沪深300 200日趋势风险状态。

## 消融结果

{_markdown_table(table)}

## 晋级判断

{"通过内部验证最低条件。" if promotion_pass else "未通过内部验证最低条件，不能晋级。"}

晋级条件在运行前固定为正超额收益、正年化收益和最大回撤不低于-20%。本报告没有从变体中挑选训练期收益最高的规则。
"""
        path.write_text(content, encoding="utf-8")

    def summary(self) -> dict[str, object]:
        import json

        path = self.store.processed_dir / "structural_v2_summary.json"
        if not path.is_file():
            raise FileNotFoundError("Run 'uv run quant-candidate run' first")
        return json.loads(path.read_text(encoding="utf-8"))


def _markdown_table(frame: pd.DataFrame) -> str:
    headers = [str(column) for column in frame.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in frame.itertuples(index=False):
        values = []
        for value in row:
            if isinstance(value, (float, np.floating)):
                values.append(f"{float(value):.6f}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)
