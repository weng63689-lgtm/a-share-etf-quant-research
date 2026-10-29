"""Run baseline signals under no-cost and stressed transaction-cost scenarios."""

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


class BacktestPipeline:
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
        engine = StrategyBacktestEngine(
            self.project_root,
            strategy_config=strategy_config,
            account_config=account_config,
        )
        scenario_specs = [("no_cost", Decimal("1"), False)] + [
            (
                f"cost_{str(multiplier).replace('.', '_')}x",
                Decimal(str(multiplier)),
                True,
            )
            for multiplier in cost_model.config.stress_multipliers
        ]
        results = [
            engine.run(
                daily=daily,
                actions=actions,
                calendar=calendar,
                benchmark=benchmark,
                signal_bundle=signal_bundle,
                rule_book=rule_book,
                cost_model=cost_model,
                scenario=scenario,
                stress_multiplier=stress,
                with_costs=with_costs,
            )
            for scenario, stress, with_costs in scenario_specs
        ]
        snapshots = pd.concat([result.snapshots for result in results], ignore_index=True)
        orders = pd.concat([result.orders for result in results], ignore_index=True)
        summaries = pd.DataFrame([result.summary for result in results])
        yearly = self._yearly_returns(snapshots)
        signals = signal_bundle.rankings.merge(
            signal_bundle.targets[["execution_date", "symbol", "target_weight"]],
            on=["execution_date", "symbol"],
            how="left",
            validate="one_to_one",
        )
        signals["target_weight"] = signals["target_weight"].fillna(0.0)
        signals["selected"] = signals["target_weight"] > 0

        errors = self._validate_outputs(snapshots, orders, signal_bundle, strategy_config)
        quality = {
            "status": "ok" if not errors else "failed",
            "errors": errors,
            "checked_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "scenarios": len(summaries),
            "snapshots": len(snapshots),
            "orders": len(orders),
            "signal_rows": len(signals),
        }
        self.store.save_processed(snapshots, "backtest_snapshots")
        self.store.save_processed(orders, "backtest_orders")
        self.store.save_processed(signals, "backtest_signals")
        self.store.save_processed(summaries, "backtest_summary")
        self.store.save_processed(yearly, "backtest_yearly")
        self.store.update_backtest_database(snapshots, orders, signals, summaries, yearly)
        payload = {
            "status": quality["status"],
            "updated_at": quality["checked_at"],
            "strategy": {
                "momentum_days": strategy_config.momentum_days,
                "trend_days": strategy_config.trend_days,
                "volatility_days": strategy_config.volatility_days,
                "selection_count": strategy_config.selection_count,
                "retention_rank": strategy_config.retention_rank,
                "target_exposure": strategy_config.target_exposure,
                "max_single_weight": strategy_config.max_single_weight,
                "min_trade_weight": strategy_config.min_trade_weight,
                "min_rebalance_turnover": strategy_config.min_rebalance_turnover,
                "max_rebalance_turnover": strategy_config.max_rebalance_turnover,
            },
            "scenarios": summaries.to_dict(orient="records"),
            "quality": quality,
        }
        self.store.save_backtest_report(payload)
        report_path = self.project_root / "runs" / "backtest_v1" / "report.md"
        self._write_markdown(report_path, summaries, yearly, quality)
        if errors:
            raise ValueError("Backtest validation failed: " + "; ".join(errors))
        return payload

    @staticmethod
    def _yearly_returns(snapshots: pd.DataFrame) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        for (scenario, year), frame in snapshots.groupby(
            ["scenario", snapshots["trade_date"].dt.year]
        ):
            returns = frame["equity"].pct_change(fill_method=None).dropna()
            rows.append(
                {
                    "scenario": scenario,
                    "year": int(year),
                    "return": float((1 + returns).prod() - 1),
                    "end_equity": float(frame["equity"].iloc[-1]),
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def _validate_outputs(
        snapshots: pd.DataFrame,
        orders: pd.DataFrame,
        signal_bundle: object,
        strategy_config: object,
    ) -> list[str]:
        errors: list[str] = []
        if not snapshots["reconciled"].all():
            errors.append("Backtest contains an unreconciled account snapshot")
        if (snapshots[["cash", "market_value", "equity"]] < 0).any().any():
            errors.append("Backtest contains negative balances")
        if (snapshots["gross_exposure"] > 1.000001).any():
            errors.append("Backtest contains leverage above 100%")
        if not orders.empty and (orders["execution_date"] <= orders["signal_date"]).any():
            errors.append("Backtest contains an order executed on or before its signal date")
        maximum_target = (
            signal_bundle.targets["target_weight"].max()
            if not signal_bundle.targets.empty
            else 0
        )
        if maximum_target > strategy_config.max_single_weight + 1e-12:
            errors.append("A target weight exceeds the single-ETF limit")
        if not np.isfinite(snapshots["equity"].to_numpy(dtype=float)).all():
            errors.append("Backtest equity contains non-finite values")
        return errors

    @staticmethod
    def _write_markdown(
        path: Path,
        summaries: pd.DataFrame,
        yearly: pd.DataFrame,
        quality: dict[str, object],
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        columns = [
            "scenario",
            "final_equity",
            "total_return",
            "annualized_return",
            "max_drawdown",
            "sharpe_ratio",
            "excess_return",
            "annualized_one_way_turnover",
            "rejected_orders",
        ]
        table = BacktestPipeline._markdown_table(summaries[columns])
        yearly_table = BacktestPipeline._markdown_table(yearly.pivot(
            index="year",
            columns="scenario",
            values="return",
        ).round(6).reset_index())
        content = f"""# 第一版ETF趋势动量轮动回测

状态：{quality["status"]}

## 场景结果

{table}

## 年度收益

{yearly_table}

## 重要说明

- 信号使用前复权收盘价，订单使用未复权下一交易日开盘价。
- T+1、整手、停牌、涨跌停、公司行动、佣金、滑点和成交容量均已接入。
- 回测起点受候选池和指标预热期影响。
- 当前ETF池按当前研究名单确定，仍存在样本选择偏差。
- 这些结果是研究基线，不是收益承诺，也不能直接视为实盘预期。
"""
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _markdown_table(frame: pd.DataFrame) -> str:
        headers = [str(column) for column in frame.columns]
        lines = [
            "| " + " | ".join(headers) + " |",
            "|" + "|".join("---" for _ in headers) + "|",
        ]
        for row in frame.itertuples(index=False):
            values = []
            for value in row:
                if isinstance(value, float):
                    values.append(f"{value:.6f}")
                else:
                    values.append(str(value))
            lines.append("| " + " | ".join(values) + " |")
        return "\n".join(lines)
