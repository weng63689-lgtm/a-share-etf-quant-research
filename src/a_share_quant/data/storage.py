"""Local Parquet and DuckDB storage for reference data."""

import json
import os
import re
from pathlib import Path
from uuid import uuid4

import duckdb
import pandas as pd


class DataStore:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.raw_dir = project_root / "data" / "raw" / "akshare"
        self.processed_dir = project_root / "data" / "processed"
        self.reference_dir = project_root / "data" / "reference"
        self.database_path = project_root / "data" / "market.duckdb"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)

    def save_raw(
        self,
        frame: pd.DataFrame,
        dataset: str,
        source: str,
        *,
        namespace: str = "akshare",
    ) -> Path:
        stamp = pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y%m%dT%H%M%S%z")
        safe_source = re.sub(r"[^A-Za-z0-9._-]+", "_", source)
        raw_dir = self.project_root / "data" / "raw" / namespace
        raw_dir.mkdir(parents=True, exist_ok=True)
        path = raw_dir / f"{dataset}_{safe_source}_{stamp}.parquet"
        self._atomic_parquet(frame, path)
        return path

    def save_processed(self, frame: pd.DataFrame, name: str) -> Path:
        path = self.processed_dir / f"{name}.parquet"
        self._atomic_parquet(frame, path)
        return path

    def load_processed(self, name: str) -> pd.DataFrame:
        path = self.processed_dir / f"{name}.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"Processed dataset does not exist: {path}")
        return pd.read_parquet(path)

    def load_universe(self, filename: str = "strategy_universe.csv") -> pd.DataFrame:
        return pd.read_csv(self.reference_dir / filename, dtype={"symbol": str})

    def update_database(
        self,
        calendar: pd.DataFrame,
        etf_master: pd.DataFrame,
        universe: pd.DataFrame,
    ) -> None:
        with duckdb.connect(str(self.database_path)) as connection:
            for name, frame in (
                ("trading_calendar", calendar),
                ("etf_master", etf_master),
                ("strategy_universe", universe),
            ):
                temporary_name = f"_{name}_frame"
                connection.register(temporary_name, frame)
                connection.execute(
                    f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM {temporary_name}"
                )
                connection.unregister(temporary_name)

    def update_history_database(
        self,
        daily: pd.DataFrame,
        coverage: pd.DataFrame,
        missing_dates: pd.DataFrame,
    ) -> None:
        """Replace strategy-readable daily tables only after full validation."""
        with duckdb.connect(str(self.database_path)) as connection:
            for name, frame in (
                ("etf_daily", daily),
                ("etf_daily_coverage", coverage),
                ("etf_daily_missing_dates", missing_dates),
            ):
                temporary_name = f"_{name}_frame"
                connection.register(temporary_name, frame)
                connection.execute(
                    f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM {temporary_name}"
                )
                connection.unregister(temporary_name)
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS etf_daily_symbol_date "
                "ON etf_daily(symbol, trade_date)"
            )

    def update_corporate_actions_database(self, actions: pd.DataFrame) -> None:
        with duckdb.connect(str(self.database_path)) as connection:
            connection.register("_etf_corporate_actions_frame", actions)
            connection.execute(
                "CREATE OR REPLACE TABLE etf_corporate_actions "
                "AS SELECT * FROM _etf_corporate_actions_frame"
            )
            connection.unregister("_etf_corporate_actions_frame")

    def update_account_database(
        self,
        snapshots: pd.DataFrame,
        ledger: pd.DataFrame,
        summaries: pd.DataFrame,
    ) -> None:
        with duckdb.connect(str(self.database_path)) as connection:
            for name, frame in (
                ("buy_hold_snapshots", snapshots),
                ("account_ledger", ledger),
                ("buy_hold_summary", summaries),
            ):
                temporary_name = f"_{name}_frame"
                connection.register(temporary_name, frame)
                connection.execute(
                    f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM {temporary_name}"
                )
                connection.unregister(temporary_name)

    def update_constraint_database(self, events: pd.DataFrame) -> None:
        with duckdb.connect(str(self.database_path)) as connection:
            connection.register("_trading_constraint_events_frame", events)
            connection.execute(
                "CREATE OR REPLACE TABLE trading_constraint_events "
                "AS SELECT * FROM _trading_constraint_events_frame"
            )
            connection.unregister("_trading_constraint_events_frame")

    def update_cost_database(self, scenarios: pd.DataFrame) -> None:
        with duckdb.connect(str(self.database_path)) as connection:
            connection.register("_cost_model_scenarios_frame", scenarios)
            connection.execute(
                "CREATE OR REPLACE TABLE cost_model_scenarios "
                "AS SELECT * FROM _cost_model_scenarios_frame"
            )
            connection.unregister("_cost_model_scenarios_frame")

    def update_benchmark_database(self, benchmark: pd.DataFrame) -> None:
        with duckdb.connect(str(self.database_path)) as connection:
            connection.register("_benchmark_daily_frame", benchmark)
            connection.execute(
                "CREATE OR REPLACE TABLE benchmark_daily "
                "AS SELECT * FROM _benchmark_daily_frame"
            )
            connection.unregister("_benchmark_daily_frame")

    def update_backtest_database(
        self,
        snapshots: pd.DataFrame,
        orders: pd.DataFrame,
        signals: pd.DataFrame,
        summaries: pd.DataFrame,
        yearly: pd.DataFrame,
    ) -> None:
        with duckdb.connect(str(self.database_path)) as connection:
            for name, frame in (
                ("backtest_snapshots", snapshots),
                ("backtest_orders", orders),
                ("backtest_signals", signals),
                ("backtest_summary", summaries),
                ("backtest_yearly", yearly),
            ):
                temporary_name = f"_{name}_frame"
                connection.register(temporary_name, frame)
                connection.execute(
                    f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM {temporary_name}"
                )
                connection.unregister(temporary_name)

    def update_robustness_database(
        self,
        scenarios: pd.DataFrame,
        parameter_grid: pd.DataFrame,
        rolling: pd.DataFrame,
        snapshots: pd.DataFrame,
        orders: pd.DataFrame,
    ) -> None:
        with duckdb.connect(str(self.database_path)) as connection:
            for name, frame in (
                ("robustness_scenarios", scenarios),
                ("robustness_parameter_grid", parameter_grid),
                ("robustness_rolling_windows", rolling),
                ("robustness_snapshots", snapshots),
                ("robustness_orders", orders),
            ):
                temporary_name = f"_{name}_frame"
                connection.register(temporary_name, frame)
                connection.execute(
                    f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM {temporary_name}"
                )
                connection.unregister(temporary_name)

    def update_diagnostics_database(
        self,
        regimes: pd.DataFrame,
        yearly: pd.DataFrame,
        selection: pd.DataFrame,
        drawdowns: pd.DataFrame,
        trades: pd.DataFrame,
    ) -> None:
        with duckdb.connect(str(self.database_path)) as connection:
            for name, frame in (
                ("diagnostics_regimes", regimes),
                ("diagnostics_yearly", yearly),
                ("diagnostics_selection", selection),
                ("diagnostics_drawdowns", drawdowns),
                ("diagnostics_trades", trades),
            ):
                temporary_name = f"_{name}_frame"
                connection.register(temporary_name, frame)
                connection.execute(
                    f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM {temporary_name}"
                )
                connection.unregister(temporary_name)

    def update_structural_database(
        self,
        scenarios: pd.DataFrame,
        snapshots: pd.DataFrame,
        orders: pd.DataFrame,
    ) -> None:
        with duckdb.connect(str(self.database_path)) as connection:
            for name, frame in (
                ("structural_scenarios", scenarios),
                ("structural_snapshots", snapshots),
                ("structural_orders", orders),
            ):
                temporary_name = f"_{name}_frame"
                connection.register(temporary_name, frame)
                connection.execute(
                    f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM {temporary_name}"
                )
                connection.unregister(temporary_name)

    def update_factor_database(
        self,
        panel: pd.DataFrame,
        detail: pd.DataFrame,
        summary: pd.DataFrame,
        *,
        prefix: str = "",
    ) -> None:
        with duckdb.connect(str(self.database_path)) as connection:
            for name, frame in (
                (f"{prefix}factor_panel", panel),
                (f"{prefix}factor_ic_detail", detail),
                (f"{prefix}factor_ic_summary", summary),
            ):
                temporary_name = f"_{name}_frame"
                connection.register(temporary_name, frame)
                connection.execute(
                    f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM {temporary_name}"
                )
                connection.unregister(temporary_name)

    def save_run_metadata(self, payload: dict[str, object]) -> Path:
        path = self.processed_dir / "reference_data_metadata.json"
        return self._atomic_json(path, payload)

    def save_history_metadata(self, payload: dict[str, object]) -> Path:
        path = self.processed_dir / "history_metadata.json"
        return self._atomic_json(path, payload)

    def save_history_quality_report(self, payload: dict[str, object]) -> Path:
        path = self.processed_dir / "history_quality_report.json"
        return self._atomic_json(path, payload)

    def save_corporate_action_report(self, payload: dict[str, object]) -> Path:
        path = self.processed_dir / "corporate_action_report.json"
        return self._atomic_json(path, payload)

    def save_buy_hold_summary(self, payload: dict[str, object]) -> Path:
        path = self.processed_dir / "buy_hold_summary.json"
        return self._atomic_json(path, payload)

    def save_account_quality_report(self, payload: dict[str, object]) -> Path:
        path = self.processed_dir / "account_model_quality_report.json"
        return self._atomic_json(path, payload)

    def save_trading_constraint_report(self, payload: dict[str, object]) -> Path:
        path = self.processed_dir / "trading_constraint_report.json"
        return self._atomic_json(path, payload)

    def save_cost_model_report(self, payload: dict[str, object]) -> Path:
        path = self.processed_dir / "cost_model_audit.json"
        return self._atomic_json(path, payload)

    def save_backtest_report(self, payload: dict[str, object]) -> Path:
        path = self.processed_dir / "backtest_summary.json"
        return self._atomic_json(path, payload)

    def save_robustness_summary(self, payload: dict[str, object]) -> Path:
        path = self.processed_dir / "robustness_summary.json"
        return self._atomic_json(path, payload)

    def save_diagnostics_report(self, payload: dict[str, object]) -> Path:
        path = self.processed_dir / "diagnostics_summary.json"
        return self._atomic_json(path, payload)

    def save_structural_report(self, payload: dict[str, object]) -> Path:
        path = self.processed_dir / "structural_v2_summary.json"
        return self._atomic_json(path, payload)

    def save_factor_report(
        self,
        payload: dict[str, object],
        *,
        prefix: str = "",
    ) -> Path:
        path = self.processed_dir / f"{prefix}factor_research_summary.json"
        return self._atomic_json(path, payload)

    def save_research_history_metadata(
        self,
        payload: dict[str, object],
        *,
        prefix: str = "",
    ) -> Path:
        path = self.processed_dir / f"{prefix}expanded_history_metadata.json"
        return self._atomic_json(path, payload)

    def save_research_action_report(
        self,
        payload: dict[str, object],
        *,
        prefix: str = "",
    ) -> Path:
        path = self.processed_dir / f"{prefix}expanded_action_report.json"
        return self._atomic_json(path, payload)

    def update_research_database(
        self,
        daily: pd.DataFrame,
        actions: pd.DataFrame,
        *,
        prefix: str = "",
    ) -> None:
        with duckdb.connect(str(self.database_path)) as connection:
            for name, frame in (
                (f"{prefix}research_etf_daily", daily),
                (f"{prefix}research_etf_corporate_actions", actions),
            ):
                temporary_name = f"_{name}_frame"
                connection.register(temporary_name, frame)
                connection.execute(
                    f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM {temporary_name}"
                )
                connection.unregister(temporary_name)

    def update_risk_budget_database(
        self,
        scenarios: pd.DataFrame,
        snapshots: pd.DataFrame,
        orders: pd.DataFrame,
        *,
        prefix: str = "",
    ) -> None:
        with duckdb.connect(str(self.database_path)) as connection:
            for name, frame in (
                (f"{prefix}risk_budget_scenarios", scenarios),
                (f"{prefix}risk_budget_snapshots", snapshots),
                (f"{prefix}risk_budget_orders", orders),
            ):
                temporary_name = f"_{name}_frame"
                connection.register(temporary_name, frame)
                connection.execute(
                    f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM {temporary_name}"
                )
                connection.unregister(temporary_name)

    def save_risk_budget_report(
        self,
        payload: dict[str, object],
        *,
        prefix: str = "",
    ) -> Path:
        path = self.processed_dir / f"{prefix}risk_budget_summary.json"
        return self._atomic_json(path, payload)

    def update_forward_validation_database(
        self,
        final_summary: pd.DataFrame,
        rolling: pd.DataFrame,
        snapshots: pd.DataFrame,
        orders: pd.DataFrame,
    ) -> None:
        with duckdb.connect(str(self.database_path)) as connection:
            for name, frame in (
                ("forward_validation_scenarios", final_summary),
                ("forward_validation_rolling", rolling),
                ("forward_validation_snapshots", snapshots),
                ("forward_validation_orders", orders),
            ):
                temporary_name = f"_{name}_frame"
                connection.register(temporary_name, frame)
                connection.execute(
                    f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM {temporary_name}"
                )
                connection.unregister(temporary_name)

    def save_forward_validation_report(self, payload: dict[str, object]) -> Path:
        path = self.processed_dir / "forward_validation_summary.json"
        return self._atomic_json(path, payload)

    def save_daily_update_report(self, payload: dict[str, object]) -> Path:
        path = self.processed_dir / "daily_update_report.json"
        return self._atomic_json(path, payload)

    def save_price_source_report(self, payload: dict[str, object]) -> Path:
        path = self.processed_dir / "a_stock_data_crosscheck.json"
        return self._atomic_json(path, payload)

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, object]) -> Path:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        os.replace(temporary, path)
        return path

