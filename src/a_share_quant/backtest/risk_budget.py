"""Compare portfolio risk-budget methods on the expanded ETF research universe."""

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


class RiskBudgetPipeline:
    def __init__(
        self,
        project_root: Path,
        *,
        daily_dataset: str = "research_etf_daily",
        universe_file: str = "research_universe_v2.csv",
        output_prefix: str = "",
        report_name: str = "risk_budget_v1",
    ) -> None:
        self.project_root = project_root
        self.store = DataStore(project_root)
        self.daily_dataset = daily_dataset
        self.universe_file = universe_file
        self.output_prefix = output_prefix
        self.report_name = report_name

    def run(self) -> dict[str, object]:
        daily = self.store.load_processed(self.daily_dataset)
        action_dataset = (
            self.daily_dataset.replace("_etf_daily", "_etf_corporate_actions")
        )
        actions = self.store.load_processed(action_dataset)
        calendar = self.store.load_processed("trading_calendar")
        benchmark = self.store.load_processed("benchmark_daily")
        universe = self.store.load_universe(self.universe_file)
        base_config = load_strategy_config(self.project_root)
        account_config = load_account_config(self.project_root)
        cost_model = load_cost_model(self.project_root)
        rule_book = load_trading_rules(
            self.project_root,
            universe_symbols=set(universe["symbol"].astype(str).str.zfill(6)),
        )
        base = replace(
            base_config,
            rebalance_frequency="monthly",
            min_trade_weight=0.04,
            min_rebalance_turnover=0.10,
            target_exposure=0.90,
            max_single_weight=0.35,
            market_filter=False,
        )
        variants = [
            ("equal_weight_90", replace(base, portfolio_method="equal_weight")),
            (
                "inverse_volatility_90",
                replace(base, portfolio_method="inverse_volatility"),
            ),
            (
                "vol_target_08",
                replace(
                    base,
                    portfolio_method="volatility_target",
                    target_volatility=0.08,
                ),
            ),
            (
                "vol_target_10",
                replace(
                    base,
                    portfolio_method="volatility_target",
                    target_volatility=0.10,
                ),
            ),
            (
                "vol_target_12",
                replace(
                    base,
                    portfolio_method="volatility_target",
                    target_volatility=0.12,
                ),
            ),
            (
                "vol_target_10_lower_exposure",
                replace(
                    base,
                    portfolio_method="volatility_target",
                    target_volatility=0.10,
                    target_exposure=0.80,
                ),
            ),
        ]
        periods = [
            ("train", None, pd.Timestamp("2018-12-31")),
            ("validation", pd.Timestamp("2019-01-01"), pd.Timestamp("2023-10-29")),
        ]
        summaries: list[dict[str, object]] = []
        snapshots_frames: list[pd.DataFrame] = []
        orders_frames: list[pd.DataFrame] = []
        for name, config in variants:
            bundle = build_signals(daily, calendar, config, benchmark=benchmark)
            for period, start, end in periods:
                result = StrategyBacktestEngine(
                    self.project_root,
                    strategy_config=config,
                    account_config=account_config,
                ).run(
                    daily=daily,
                    actions=actions,
                    calendar=calendar,
                    benchmark=benchmark,
                    signal_bundle=bundle,
                    rule_book=rule_book,
                    cost_model=cost_model,
                    scenario=f"{name}_{period}",
                    stress_multiplier=Decimal("1"),
                    with_costs=True,
                    start_date=start,
                    end_date=end,
                )
                summaries.append(
                    {
                        "variant": name,
                        "period": period,
                        "target_volatility": (
                            config.target_volatility
                            if config.portfolio_method == "volatility_target"
                            else None
                        ),
                        **result.summary,
                    }
                )
                snapshots_frames.append(result.snapshots)
                orders_frames.append(result.orders)

        primary_config = next(config for name, config in variants if name == "vol_target_10")
        primary_bundle = build_signals(
            daily,
            calendar,
            primary_config,
            benchmark=benchmark,
        )
        primary_stress_results: list[dict[str, object]] = []
        for stress in (Decimal("2"), Decimal("3")):
            result = StrategyBacktestEngine(
                self.project_root,
                strategy_config=primary_config,
                account_config=account_config,
            ).run(
                daily=daily,
                actions=actions,
                calendar=calendar,
                benchmark=benchmark,
                signal_bundle=primary_bundle,
                rule_book=rule_book,
                cost_model=cost_model,
                scenario=f"vol_target_10_cost_{stress}x_validation",
                stress_multiplier=stress,
                with_costs=True,
                start_date=pd.Timestamp("2019-01-01"),
                end_date=pd.Timestamp("2023-10-29"),
            )
            row = {
                "variant": f"vol_target_10_cost_{stress}x",
                "period": "validation",
                "target_volatility": primary_config.target_volatility,
                **result.summary,
            }
            summaries.append(row)
            primary_stress_results.append(row)
            snapshots_frames.append(result.snapshots)
            orders_frames.append(result.orders)

        summary = pd.DataFrame(summaries)
        snapshots = pd.concat(snapshots_frames, ignore_index=True)
        orders = pd.concat(orders_frames, ignore_index=True)
        primary = summary[
            (summary["variant"] == "vol_target_10") & (summary["period"] == "validation")
        ].iloc[0]
        cost_stress_pass = bool(
            all(
                row["annualized_return"] > 0 and row["max_drawdown"] >= -0.20
                for row in primary_stress_results
            )
        )
        promotion_pass = bool(
            primary["excess_return"] > 0
            and primary["max_drawdown"] >= -0.20
            and primary["annualized_return"] > 0
            and cost_stress_pass
        )
        errors: list[str] = []
        if not snapshots["reconciled"].all():
            errors.append("Risk-budget backtest contains an unreconciled snapshot")
        if not np.isfinite(summary["annualized_return"]).all():
            errors.append("Risk-budget summary contains non-finite returns")
        payload = {
            "status": "ok" if not errors else "failed",
            "updated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "universe": self.universe_file,
            "split": {
                "train_end": "2018-12-31",
                "validation_start": "2019-01-01",
                "validation_end": "2023-10-29",
                "final_out_of_sample_used": False,
            },
            "primary_variant": "vol_target_10",
            "summary": summary.to_dict(orient="records"),
            "promotion_pass": promotion_pass,
            "cost_stress_pass": cost_stress_pass,
            "primary_validation": {
                "total_return": float(primary["total_return"]),
                "annualized_return": float(primary["annualized_return"]),
                "max_drawdown": float(primary["max_drawdown"]),
                "excess_return": float(primary["excess_return"]),
                "annualized_one_way_turnover": float(primary["annualized_one_way_turnover"]),
            },
            "errors": errors,
        }
        self.store.save_processed(summary, f"{self.output_prefix}risk_budget_scenarios")
        self.store.save_processed(
            snapshots,
            f"{self.output_prefix}risk_budget_snapshots",
        )
        self.store.save_processed(orders, f"{self.output_prefix}risk_budget_orders")
        self.store.update_risk_budget_database(
            summary,
            snapshots,
            orders,
            prefix=self.output_prefix,
        )
        self.store.save_risk_budget_report(payload, prefix=self.output_prefix)
        self._write_report(
            self.project_root / "runs" / self.report_name / "report.md",
            summary,
            promotion_pass,
        )
        if errors:
            raise ValueError("Risk-budget validation failed: " + "; ".join(errors))
        return payload

    @staticmethod
    def _write_report(path: Path, summary: pd.DataFrame, promotion_pass: bool) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        table = _markdown_table(
            summary[
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
        )
        content = f"""# 扩展池风险预算测试

## 固定规则

- 月度调仓，最多3只ETF。
- 使用相对200日均线的绝对趋势过滤。
- 训练期截至2018-12-31，内部验证期2019-01-01至2023-10-29。
- 最终样本外没有参与风险预算方法选择。
- 主候选固定为10%目标波动率、90%最大仓位，不按验证结果更换。

## 结果

{table}

## 晋级判断

{"通过内部验证最低条件。" if promotion_pass else "未通过内部验证最低条件。"}

晋级条件在运行前固定为正收益、正超额和最大回撤不低于-20%。
"""
        path.write_text(content, encoding="utf-8")

    def summary(self) -> dict[str, object]:
        import json

        path = (
            self.store.processed_dir
            / f"{self.output_prefix}risk_budget_summary.json"
        )
        if not path.is_file():
            raise FileNotFoundError("Run 'uv run quant-risk-budget run' first")
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
