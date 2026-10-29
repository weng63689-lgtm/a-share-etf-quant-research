"""Out-of-sample, parameter-neighborhood, and rolling-window robustness tests."""

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


class RobustnessPipeline:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.store = DataStore(project_root)

    def run(self) -> dict[str, object]:
        daily = self.store.load_processed("etf_daily")
        actions = self.store.load_processed("etf_corporate_actions")
        calendar = self.store.load_processed("trading_calendar")
        benchmark = self.store.load_processed("benchmark_daily")
        universe = self.store.load_universe()
        strategy_config = load_strategy_config(self.project_root)
        account_config = load_account_config(self.project_root)
        cost_model = load_cost_model(self.project_root)
        rule_book = load_trading_rules(
            self.project_root,
            universe_symbols=set(universe["symbol"].astype(str).str.zfill(6)),
        )
        signal_bundle = build_signals(daily, calendar, strategy_config)
        development_end = pd.Timestamp("2023-10-29")
        oos_start = pd.Timestamp("2023-10-30")
        scenarios: list[dict[str, object]] = []
        scenario_results = []

        for label, drawdown_control, start, end in (
            ("development", False, None, development_end),
            ("out_of_sample", False, oos_start, None),
            ("development_risk_control", True, None, development_end),
            ("out_of_sample_risk_control", True, oos_start, None),
        ):
            result = StrategyBacktestEngine(
                self.project_root,
                strategy_config=strategy_config,
                account_config=account_config,
                drawdown_control=drawdown_control,
            ).run(
                daily=daily,
                actions=actions,
                calendar=calendar,
                benchmark=benchmark,
                signal_bundle=signal_bundle,
                rule_book=rule_book,
                cost_model=cost_model,
                scenario=label,
                stress_multiplier=Decimal("1"),
                with_costs=True,
                start_date=start,
                end_date=end,
            )
            scenarios.append(result.summary)
            scenario_results.append(result)

        parameter_rows: list[dict[str, object]] = []
        for momentum_days in (90, 120, 160):
            for trend_days in (120, 200, 250):
                for min_rebalance in (0.05, 0.10):
                    config = replace(
                        strategy_config,
                        momentum_days=momentum_days,
                        trend_days=trend_days,
                        momentum_windows=(momentum_days,),
                        trend_windows=(trend_days,),
                        min_rebalance_turnover=min_rebalance,
                    )
                    bundle = build_signals(daily, calendar, config)
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
                        scenario=(
                            f"mom{momentum_days}_trend{trend_days}_"
                            f"turnover{str(min_rebalance).replace('.', '_')}"
                        ),
                        stress_multiplier=Decimal("1"),
                        with_costs=True,
                        end_date=development_end,
                    )
                    parameter_rows.append(
                        {
                            **result.summary,
                            "momentum_days": momentum_days,
                            "trend_days": trend_days,
                            "min_rebalance_turnover": min_rebalance,
                        }
                    )
        parameter_grid = pd.DataFrame(parameter_rows)

        rolling_rows: list[dict[str, object]] = []
        for start in pd.to_datetime(
            ["2016-01-01", "2018-01-01", "2020-01-01", "2022-01-01", "2023-10-30"]
        ):
            end = min(
                start + pd.DateOffset(years=3) - pd.Timedelta(days=1),
                daily["trade_date"].max(),
            )
            result = StrategyBacktestEngine(
                self.project_root,
                strategy_config=strategy_config,
                account_config=account_config,
            ).run(
                daily=daily,
                actions=actions,
                calendar=calendar,
                benchmark=benchmark,
                signal_bundle=signal_bundle,
                rule_book=rule_book,
                cost_model=cost_model,
                scenario=f"rolling_{start.year}_{end.year}",
                stress_multiplier=Decimal("1"),
                with_costs=True,
                start_date=start,
                end_date=end,
            )
            rolling_rows.append(
                {
                    **result.summary,
                    "window_start": start.date(),
                    "window_end": end.date(),
                }
            )
        rolling = pd.DataFrame(rolling_rows)

        stability = {
            "configurations": len(parameter_grid),
            "positive_excess_share": float(
                (parameter_grid["excess_return"] > 0).mean()
            ),
            "median_annualized_return": float(
                parameter_grid["annualized_return"].median()
            ),
            "median_max_drawdown": float(parameter_grid["max_drawdown"].median()),
            "best_max_drawdown": float(parameter_grid["max_drawdown"].max()),
            "worst_max_drawdown": float(parameter_grid["max_drawdown"].min()),
            "meets_drawdown_target_share": float(
                (parameter_grid["max_drawdown"] >= -0.20).mean()
            ),
        }
        summary_frame = pd.DataFrame(scenarios)
        errors: list[str] = []
        if not summaries_are_reconciled(scenario_results):
            errors.append("A robustness backtest contains an unreconciled snapshot")
        if not np.isfinite(parameter_grid["annualized_return"]).all():
            errors.append("Parameter grid contains non-finite returns")
        payload = {
            "status": "ok" if not errors else "failed",
            "updated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "split": {
                "development_end": development_end.date(),
                "out_of_sample_start": oos_start.date(),
            },
            "scenarios": summary_frame.to_dict(orient="records"),
            "stability": stability,
            "errors": errors,
        }

        snapshots = pd.concat(
            [result.snapshots for result in scenario_results],
            ignore_index=True,
        )
        orders = pd.concat(
            [result.orders for result in scenario_results],
            ignore_index=True,
        )
        self.store.save_processed(summary_frame, "robustness_scenarios")
        self.store.save_processed(parameter_grid, "robustness_parameter_grid")
        self.store.save_processed(rolling, "robustness_rolling_windows")
        self.store.save_processed(snapshots, "robustness_snapshots")
        self.store.save_processed(orders, "robustness_orders")
        self.store.update_robustness_database(
            summary_frame,
            parameter_grid,
            rolling,
            snapshots,
            orders,
        )
        self.store.save_robustness_summary(payload)
        self._write_report(
            self.project_root / "runs" / "robustness_v1" / "report.md",
            summary_frame,
            parameter_grid,
            rolling,
            stability,
            errors,
        )
        if errors:
            raise ValueError("Robustness validation failed: " + "; ".join(errors))
        return {
            "status": "ok",
            "updated_at": payload["updated_at"],
            "split": payload["split"],
            "stability": stability,
            "scenarios": summary_frame[
                [
                    "scenario",
                    "final_equity",
                    "total_return",
                    "annualized_return",
                    "max_drawdown",
                    "excess_return",
                ]
            ].to_dict(orient="records"),
            "rolling_windows": rolling[
                [
                    "window_start",
                    "window_end",
                    "total_return",
                    "max_drawdown",
                    "excess_return",
                ]
            ].to_dict(orient="records"),
        }

    @staticmethod
    def _write_report(
        path: Path,
        scenarios: pd.DataFrame,
        parameter_grid: pd.DataFrame,
        rolling: pd.DataFrame,
        stability: dict[str, object],
        errors: list[str],
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        scenario_table = _markdown_table(
            scenarios[
                [
                    "scenario",
                    "total_return",
                    "annualized_return",
                    "max_drawdown",
                    "sharpe_ratio",
                    "excess_return",
                    "annualized_one_way_turnover",
                ]
            ]
        )
        rolling_table = _markdown_table(
            rolling[
                [
                    "window_start",
                    "window_end",
                    "total_return",
                    "max_drawdown",
                    "excess_return",
                ]
            ]
        )
        parameter_table = _markdown_table(
            parameter_grid[
                [
                    "momentum_days",
                    "trend_days",
                    "min_rebalance_turnover",
                    "annualized_return",
                    "max_drawdown",
                    "excess_return",
                ]
            ]
        )
        content = f"""# 策略稳健性检查

状态：{"ok" if not errors else "failed"}

## 样本切分

- 参数邻域只在2023-10-29及以前的数据上比较。
- 2023-10-30以后作为固定规则样本外测试。
- 固定规则滚动窗口分别重置账户，并使用相同成本模型。

## 主策略场景

{scenario_table}

## 滚动窗口

{rolling_table}

## 参数邻域

{parameter_table}

## 稳定性摘要

- 参数组合数量：{stability["configurations"]}
- 正超额收益组合占比：{stability["positive_excess_share"]:.2%}
- 年化收益中位数：{stability["median_annualized_return"]:.2%}
- 最大回撤中位数：{stability["median_max_drawdown"]:.2%}
- 满足20%回撤目标的组合占比：{stability["meets_drawdown_target_share"]:.2%}

稳健性结果用于判断策略是否值得继续研究，不用于从邻域中挑选历史收益最高的参数。
"""
        path.write_text(content, encoding="utf-8")

    def summary(self) -> dict[str, object]:
        import json

        path = self.store.processed_dir / "robustness_summary.json"
        if not path.is_file():
            raise FileNotFoundError("Run 'uv run quant-robustness run' first")
        return json.loads(path.read_text(encoding="utf-8"))


def summaries_are_reconciled(results: list[object]) -> bool:
    return all(result.snapshots["reconciled"].all() for result in results)


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
