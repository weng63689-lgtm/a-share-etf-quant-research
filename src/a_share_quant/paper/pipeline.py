"""Idempotent offline paper-trading loop over validated local market data."""

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pandas as pd

from a_share_quant.accounting.config import load_account_config
from a_share_quant.backtest.engine import StrategyBacktestEngine
from a_share_quant.data.history import infer_latest_completed_session
from a_share_quant.data.storage import DataStore
from a_share_quant.execution.costs import load_cost_model
from a_share_quant.execution.rules import load_trading_rules
from a_share_quant.paper.store import PaperStore
from a_share_quant.strategy.config import load_strategy_config
from a_share_quant.strategy.momentum import build_signals


class PaperTradingPipeline:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.store = PaperStore(project_root)
        self.data_store = DataStore(project_root)
        self.config_path = project_root / "configs" / "frozen" / "multi_asset_v1.yaml"

    def initialize(self, *, start_date: str) -> dict[str, object]:
        if self.store.state_path.exists():
            raise FileExistsError(
                "Paper state already exists. Use 'quant-paper status' or continue with 'run'."
            )
        calendar = self.data_store.load_processed("trading_calendar")
        open_dates = pd.DatetimeIndex(
            pd.to_datetime(
                calendar.loc[calendar["is_open"], "calendar_date"]
            ).sort_values()
        )
        requested = pd.Timestamp(start_date).normalize()
        eligible = open_dates[open_dates >= requested]
        if eligible.empty:
            raise ValueError("Paper start date is outside the trading calendar")
        actual_start = pd.Timestamp(eligible[0]).normalize()
        account_config = load_account_config(self.project_root)
        state = {
            "schema_version": 1,
            "mode": "paper",
            "live_trading_enabled": False,
            "broker_connected": False,
            "strategy": "multi_asset_v1",
            "config_sha256": self._file_hash(self.config_path),
            "algorithm_sha256": self._algorithm_hash(),
            "initial_cash": account_config.initial_cash,
            "start_date": actual_start.date().isoformat(),
            "last_processed_date": None,
            "status": "ready",
            "data_fingerprint": None,
            "last_report": {},
        }
        path = self.store.save_state(state)
        return {**state, "state_path": str(path)}

    def run(self, *, as_of: str) -> dict[str, object]:
        state = self.store.load_state()
        if state is None:
            raise FileNotFoundError("Run 'uv run quant-paper init --start-date YYYY-MM-DD' first")
        self._verify_frozen_versions(state)
        as_of_date = pd.Timestamp(as_of).normalize()
        calendar = self.data_store.load_processed("trading_calendar")
        open_dates = pd.DatetimeIndex(
            pd.to_datetime(
                calendar.loc[calendar["is_open"], "calendar_date"]
            ).sort_values()
        )
        if as_of_date not in open_dates:
            raise ValueError(f"{as_of_date.date()} is not a trading day")

        daily = self.data_store.load_processed("multi_asset_research_etf_daily")
        actions = self.data_store.load_processed("multi_asset_research_etf_corporate_actions")
        benchmark = self.data_store.load_processed("benchmark_daily")
        if pd.to_datetime(daily["trade_date"]).max() < as_of_date:
            raise ValueError(
                f"ETF history must cover {as_of_date.date()} before paper trading can run"
            )

        previous = state.get("last_processed_date")
        if previous is not None:
            previous_date = pd.Timestamp(previous)
            previous_fingerprint = self._data_fingerprint(
                daily,
                actions,
                benchmark,
                through=previous_date,
            )
            if state.get("data_fingerprint") != previous_fingerprint:
                raise ValueError(
                    "Historical data changed after it was processed. "
                    "Paper state stopped to prevent silent backfill."
                )
            if as_of_date < previous_date:
                return {
                    "status": "already_processed",
                    "last_processed_date": previous,
                    "live_trading_enabled": False,
                }

        universe = self.data_store.load_universe("research_universe_v3.csv")
        strategy_config = load_strategy_config(
            self.project_root,
            override_file=self.config_path,
        )
        account_config = load_account_config(self.project_root)
        cost_model = load_cost_model(self.project_root)
        rule_book = load_trading_rules(
            self.project_root,
            universe_symbols=set(universe["symbol"].astype(str).str.zfill(6)),
        )
        bundle = build_signals(
            daily,
            calendar,
            strategy_config,
            benchmark=benchmark,
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
            signal_bundle=bundle,
            rule_book=rule_book,
            cost_model=cost_model,
            scenario="paper",
            stress_multiplier=Decimal("1"),
            with_costs=True,
            start_date=pd.Timestamp(str(state["start_date"])),
            end_date=as_of_date,
        )
        snapshots = result.snapshots.copy()
        orders = result.orders.copy()
        if not orders.empty:
            orders["order_id"] = orders.apply(self._order_id, axis=1)
            orders["mode"] = "paper"
            orders["live_trading_enabled"] = False
        else:
            orders["order_id"] = pd.Series(dtype=str)
            orders["mode"] = pd.Series(dtype=str)
            orders["live_trading_enabled"] = pd.Series(dtype=bool)
        targets = self._target_frame(bundle, as_of_date)
        snapshots_path = self.store.save_snapshots(snapshots)
        orders_path = self.store.save_orders(orders)
        targets_path = self.store.save_targets(targets)

        current_snapshot = snapshots.iloc[-1]
        current_orders = (
            orders[orders["execution_date"] == as_of_date]
            if not orders.empty
            else orders
        )
        alerts = self._alerts(current_snapshot, current_orders)
        next_dates = open_dates[open_dates > as_of_date]
        next_trading_date = (
            pd.Timestamp(next_dates[0]).date().isoformat()
            if not next_dates.empty
            else None
        )
        run_record = {
            "as_of": as_of_date,
            "processed_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "cash": float(current_snapshot["cash"]),
            "equity": float(current_snapshot["equity"]),
            "holding_count": int(current_snapshot["holding_count"]),
            "orders": len(current_orders),
            "rejected_orders": int((current_orders["status"] == "REJECTED").sum())
            if not current_orders.empty
            else 0,
            "alerts": json.dumps(alerts, ensure_ascii=False),
        }
        old_runs = self.store.load_runs()
        runs = (
            pd.concat([old_runs, pd.DataFrame([run_record])], ignore_index=True)
            .drop_duplicates("as_of", keep="last")
            .sort_values("as_of")
        )
        runs_path = self.store.save_runs(runs)
        state.update(
            {
                "last_processed_date": as_of_date.date().isoformat(),
                "status": "active",
                "data_fingerprint": self._data_fingerprint(
                    daily,
                    actions,
                    benchmark,
                    through=as_of_date,
                ),
                "next_trading_date": next_trading_date,
                "last_report": {
                    "as_of": as_of_date.date().isoformat(),
                    "cash": float(current_snapshot["cash"]),
                    "market_value": float(current_snapshot["market_value"]),
                    "equity": float(current_snapshot["equity"]),
                    "holding_count": int(current_snapshot["holding_count"]),
                    "reconciled": bool(current_snapshot["reconciled"]),
                    "orders": len(current_orders),
                    "rejected_orders": run_record["rejected_orders"],
                    "alerts": alerts,
                },
            }
        )
        state_path = self.store.save_state(state)
        self._update_database(snapshots, orders, targets, runs)
        report_path = self.store.runs_dir / "latest_report.md"
        self._write_report(report_path, state, snapshots, orders)
        return {
            **state,
            "state_path": str(state_path),
            "snapshots_path": str(snapshots_path),
            "orders_path": str(orders_path),
            "targets_path": str(targets_path),
            "runs_path": str(runs_path),
            "report_path": str(report_path),
        }

    def status(self) -> dict[str, object]:
        state = self.store.load_state()
        if state is None:
            raise FileNotFoundError("Paper trading has not been initialized")
        return state

    def run_latest(self) -> dict[str, object]:
        calendar = self.data_store.load_processed("trading_calendar")
        latest = infer_latest_completed_session(calendar)
        if latest is None:
            raise ValueError("Unable to infer the latest completed trading session")
        return self.run(as_of=latest.date().isoformat())

    def _verify_frozen_versions(self, state: dict[str, object]) -> None:
        if state.get("config_sha256") != self._file_hash(self.config_path):
            raise ValueError("Frozen configuration changed; paper state stopped")
        if state.get("algorithm_sha256") != self._algorithm_hash():
            raise ValueError("Frozen strategy implementation changed; paper state stopped")

    def _algorithm_hash(self) -> str:
        paths = [
            self.config_path,
            self.project_root / "configs" / "trading_rules.csv",
            self.project_root / "src" / "a_share_quant" / "backtest" / "engine.py",
            self.project_root / "src" / "a_share_quant" / "execution" / "order.py",
            self.project_root / "src" / "a_share_quant" / "execution" / "costs.py",
            self.project_root / "src" / "a_share_quant" / "strategy" / "momentum.py",
        ]
        digest = hashlib.sha256()
        for path in paths:
            digest.update(path.read_bytes())
        return digest.hexdigest()

    @staticmethod
    def _file_hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _data_fingerprint(
        daily: pd.DataFrame,
        actions: pd.DataFrame,
        benchmark: pd.DataFrame,
        *,
        through: pd.Timestamp,
    ) -> str:
        daily_part = daily[pd.to_datetime(daily["trade_date"]) <= through].sort_values(
            ["symbol", "trade_date"]
        )
        action_part = actions[
            pd.to_datetime(actions["event_date"]) <= through
        ].sort_values(["symbol", "event_date", "action_type"])
        benchmark_part = benchmark[
            pd.to_datetime(benchmark["trade_date"]) <= through
        ].sort_values("trade_date")
        daily_columns = [
            "symbol",
            "exchange",
            "exchange_symbol",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "turnover",
            "amplitude_pct",
            "change_pct",
            "change_amount",
            "turnover_rate_pct",
            "adj_open",
            "adj_high",
            "adj_low",
            "adj_close",
            "adjustment_factor",
            "adjustment_method",
        ]
        action_columns = [
            "symbol",
            "event_date",
            "action_type",
            "cash_per_unit",
            "share_multiplier",
        ]
        benchmark_columns = [
            "benchmark",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
        digest = hashlib.sha256()
        for frame, columns in (
            (daily_part, daily_columns),
            (action_part, action_columns),
            (benchmark_part, benchmark_columns),
        ):
            stable_frame = frame[[column for column in columns if column in frame.columns]]
            digest.update(
                pd.util.hash_pandas_object(stable_frame, index=False).to_numpy().tobytes()
            )
        return digest.hexdigest()

    @staticmethod
    def _target_frame(bundle: object, as_of: pd.Timestamp) -> pd.DataFrame:
        rows = []
        for execution_date, weights in bundle.execution_targets.items():
            if execution_date > as_of:
                continue
            signal_date = bundle.execution_signals[execution_date]
            for symbol, weight in weights.items():
                rows.append(
                    {
                        "signal_date": signal_date,
                        "execution_date": execution_date,
                        "symbol": symbol,
                        "target_weight": weight,
                    }
                )
        return pd.DataFrame(
            rows,
            columns=["signal_date", "execution_date", "symbol", "target_weight"],
        )

    @staticmethod
    def _order_id(row: pd.Series) -> str:
        raw = "|".join(
            [
                str(row["signal_date"]),
                str(row["execution_date"]),
                str(row["symbol"]),
                str(row["side"]),
                f"{float(row['requested_quantity']):.8f}",
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

    @staticmethod
    def _alerts(snapshot: pd.Series, orders: pd.DataFrame) -> list[str]:
        alerts: list[str] = []
        if not bool(snapshot["reconciled"]):
            alerts.append("ACCOUNT_NOT_RECONCILED")
        if int(snapshot["holding_count"]) > 3:
            alerts.append("TEMPORARY_HOLDING_COUNT_ABOVE_TARGET")
        if not orders.empty and (orders["status"] == "REJECTED").any():
            alerts.append("ORDERS_REJECTED")
        return alerts

    def _update_database(
        self,
        snapshots: pd.DataFrame,
        orders: pd.DataFrame,
        targets: pd.DataFrame,
        runs: pd.DataFrame,
    ) -> None:
        import duckdb

        with duckdb.connect(str(self.data_store.database_path)) as connection:
            for name, frame in (
                ("paper_snapshots", snapshots),
                ("paper_orders", orders),
                ("paper_targets", targets),
                ("paper_runs", runs),
            ):
                temporary = f"_{name}_frame"
                connection.register(temporary, frame)
                connection.execute(
                    f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM {temporary}"
                )
                connection.unregister(temporary)

    @staticmethod
    def _write_report(
        path: Path,
        state: dict[str, object],
        snapshots: pd.DataFrame,
        orders: pd.DataFrame,
    ) -> None:
        latest = snapshots.iloc[-1]
        latest_date = pd.Timestamp(latest["trade_date"]).date()
        day_orders = orders[orders["execution_date"] == pd.Timestamp(latest_date)]
        if day_orders.empty:
            orders_table = "无"
        else:
            orders_table = _markdown_table(
                day_orders[
                    [
                        "order_id",
                        "symbol",
                        "side",
                        "requested_quantity",
                        "status",
                        "reason",
                        "total_cost",
                    ]
                ]
            )
        content = f"""# 模拟盘日报

日期：{latest_date}

- 模拟模式：paper
- 真实交易：关闭
- 策略：{state["strategy"]}
- 配置哈希：`{state["config_sha256"]}`
- 代码指纹：`{state["algorithm_sha256"]}`
- 现金：{float(latest["cash"]):,.2f}
- 持仓市值：{float(latest["market_value"]):,.2f}
- 总权益：{float(latest["equity"]):,.2f}
- 持仓数量：{int(latest["holding_count"])}
- 当日订单：{len(day_orders)}
- 对账状态：{"通过" if bool(latest["reconciled"]) else "失败"}

## 当日订单

{orders_table}
"""
        path.write_text(content, encoding="utf-8")


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "无"
    headers = [str(column) for column in frame.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in frame.itertuples(index=False):
        values = []
        for value in row:
            if isinstance(value, float):
                values.append(f"{value:.2f}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)
