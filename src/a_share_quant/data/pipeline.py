"""Reference data update pipeline with provenance and validation."""

from datetime import date
from pathlib import Path

import pandas as pd

from a_share_quant.data.normalize import normalize_etf_snapshot, normalize_trade_calendar
from a_share_quant.data.providers.akshare_provider import AkShareProvider
from a_share_quant.data.providers.base import ReferenceDataProvider
from a_share_quant.data.storage import DataStore
from a_share_quant.data.validation import validate_reference_data


class DataPipeline:
    def __init__(
        self,
        project_root: Path,
        provider: ReferenceDataProvider | None = None,
    ) -> None:
        self.store = DataStore(project_root)
        self.provider = provider or AkShareProvider()

    def update_all(self, *, calendar_start: str = "2005-01-01") -> dict[str, object]:
        calendar_result = self.provider.fetch_open_trade_dates()
        self.store.save_raw(calendar_result.frame, "trade_dates", calendar_result.source)
        calendar = normalize_trade_calendar(
            calendar_result.frame,
            source=calendar_result.source,
            start_date=calendar_start,
        )

        etf_result = self.provider.fetch_etf_snapshot()
        raw_etf_path = self.store.save_raw(etf_result.frame, "etf_snapshot", etf_result.source)
        etf_master = normalize_etf_snapshot(etf_result.frame, source=etf_result.source)
        universe = self.store.load_universe()

        errors = validate_reference_data(calendar, etf_master, universe)
        if errors:
            raise ValueError("Reference data validation failed: " + "; ".join(errors))

        calendar_path = self.store.save_processed(calendar, "trading_calendar")
        master_path = self.store.save_processed(etf_master, "etf_master")
        self.store.update_database(calendar, etf_master, universe)

        summary: dict[str, object] = {
            "status": "ok",
            "updated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "calendar_source": calendar_result.source,
            "calendar_start": calendar["calendar_date"].min().date(),
            "calendar_end": calendar["calendar_date"].max().date(),
            "open_days": int(calendar["is_open"].sum()),
            "etf_source": etf_result.source,
            "etf_rows": len(etf_master),
            "strategy_universe_rows": len(universe),
            "raw_etf_path": str(raw_etf_path),
            "calendar_path": str(calendar_path),
            "master_path": str(master_path),
            "database_path": str(self.store.database_path),
        }
        self.store.save_run_metadata(summary)
        return summary

    def validate(self, *, today: date | None = None) -> list[str]:
        calendar = self.store.load_processed("trading_calendar")
        etf_master = self.store.load_processed("etf_master")
        universe = self.store.load_universe()
        return validate_reference_data(calendar, etf_master, universe, today=today)

    def summary(self) -> dict[str, object]:
        calendar = self.store.load_processed("trading_calendar")
        etf_master = self.store.load_processed("etf_master")
        universe = self.store.load_universe()
        return {
            "calendar_start": calendar["calendar_date"].min().date(),
            "calendar_end": calendar["calendar_date"].max().date(),
            "open_days": int(calendar["is_open"].sum()),
            "etf_rows": len(etf_master),
            "etf_source": sorted(etf_master["source"].dropna().unique().tolist()),
            "strategy_universe_rows": len(universe),
            "strategy_universe": universe[["symbol", "name", "tracking_index"]].to_dict(
                orient="records"
            ),
        }

