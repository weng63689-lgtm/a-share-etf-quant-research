"""End-to-end candidate ETF daily-history ingestion pipeline."""

from datetime import date
from pathlib import Path

import pandas as pd

from a_share_quant.data.history import (
    infer_latest_completed_session,
    normalize_etf_history,
    validate_etf_history,
)
from a_share_quant.data.history_config import HistoryConfig, load_history_config
from a_share_quant.data.providers.akshare_provider import AkShareProvider
from a_share_quant.data.providers.base import DailyHistoryProvider
from a_share_quant.data.storage import DataStore


class HistoryPipeline:
    def __init__(
        self,
        project_root: Path,
        provider: DailyHistoryProvider | None = None,
        config: HistoryConfig | None = None,
    ) -> None:
        self.store = DataStore(project_root)
        self.provider = provider or AkShareProvider()
        self.config = config or load_history_config(project_root)

    def update_history(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        as_of: date | None = None,
    ) -> dict[str, object]:
        """Fetch all universe symbols and atomically publish only validated history."""
        calendar = self.store.load_processed("trading_calendar")
        universe = self.store.load_universe()
        if universe.empty:
            raise ValueError("Strategy universe is empty")

        fetch_start = start_date or self.config.start_date
        fetch_end = end_date or pd.Timestamp.now(tz="Asia/Shanghai").date()
        if fetch_start > fetch_end:
            raise ValueError("History start date must not be after end date")

        normalized_frames: list[pd.DataFrame] = []
        for row in universe.itertuples(index=False):
            history = self.provider.fetch_etf_daily(
                str(row.symbol).zfill(6),
                str(row.exchange),
                start_date=fetch_start,
                end_date=fetch_end,
            )
            self.store.save_raw(
                history.raw,
                f"etf_daily_raw_{row.symbol}",
                history.source,
            )
            self.store.save_raw(
                history.adjusted,
                f"etf_daily_qfq_{row.symbol}",
                history.source,
            )
            normalized_frames.append(
                normalize_etf_history(
                    history.raw,
                    history.adjusted,
                    symbol=str(row.symbol).zfill(6),
                    exchange=str(row.exchange),
                    source=history.source,
                    retrieved_at=history.retrieved_at,
                )
            )

        daily = (
            pd.concat(normalized_frames, ignore_index=True)
            .sort_values(["symbol", "trade_date"])
            .reset_index(drop=True)
        )
        expected_date = self._expected_date(calendar, as_of)
        report = validate_etf_history(
            daily,
            calendar,
            universe,
            latest_expected_date=expected_date,
            min_coverage_ratio=self.config.min_coverage_ratio,
            max_missing_trade_days=self.config.max_missing_trade_days,
            max_missing_gap_days=self.config.max_missing_gap_days,
            max_daily_return_abs=self.config.max_daily_return_abs,
        )
        report_payload = {
            **report.to_dict(),
            "checked_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "history_start_requested": fetch_start.isoformat(),
            "history_end_requested": fetch_end.isoformat(),
            "min_coverage_ratio": self.config.min_coverage_ratio,
            "max_missing_trade_days": self.config.max_missing_trade_days,
            "max_missing_gap_days": self.config.max_missing_gap_days,
            "max_daily_return_abs": self.config.max_daily_return_abs,
        }
        quality_report_path = self.store.save_history_quality_report(report_payload)
        if report.errors:
            raise ValueError("Daily history validation failed: " + "; ".join(report.errors))

        daily_path = self.store.save_processed(daily, "etf_daily")
        coverage_path = self.store.save_processed(report.coverage, "etf_daily_coverage")
        missing_path = self.store.save_processed(
            report.missing_dates,
            "etf_daily_missing_dates",
        )
        self.store.update_history_database(daily, report.coverage, report.missing_dates)

        summary: dict[str, object] = {
            "status": "ok",
            "updated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "symbols": int(daily["symbol"].nunique()),
            "rows": len(daily),
            "start_date": daily["trade_date"].min().date(),
            "end_date": daily["trade_date"].max().date(),
            "latest_expected_date": (
                expected_date.date() if expected_date is not None else None
            ),
            "missing_rows": len(report.missing_dates),
            "coverage": report.coverage.to_dict(orient="records"),
            "daily_path": str(daily_path),
            "coverage_path": str(coverage_path),
            "missing_dates_path": str(missing_path),
            "quality_report_path": str(quality_report_path),
            "database_path": str(self.store.database_path),
            "source": sorted(daily["source"].dropna().unique().tolist()),
        }
        self.store.save_history_metadata(summary)
        return summary

    def validate_history(self, *, as_of: date | None = None) -> dict[str, object]:
        daily = self.store.load_processed("etf_daily")
        calendar = self.store.load_processed("trading_calendar")
        universe = self.store.load_universe()
        expected_date = self._expected_date(calendar, as_of)
        report = validate_etf_history(
            daily,
            calendar,
            universe,
            latest_expected_date=expected_date,
            min_coverage_ratio=self.config.min_coverage_ratio,
            max_missing_trade_days=self.config.max_missing_trade_days,
            max_missing_gap_days=self.config.max_missing_gap_days,
            max_daily_return_abs=self.config.max_daily_return_abs,
        )
        payload = {
            **report.to_dict(),
            "checked_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        }
        path = self.store.save_history_quality_report(payload)
        return {**payload, "quality_report_path": str(path)}

    def summary(self) -> dict[str, object]:
        daily = self.store.load_processed("etf_daily")
        coverage = self.store.load_processed("etf_daily_coverage")
        return {
            "symbols": int(daily["symbol"].nunique()),
            "rows": len(daily),
            "start_date": daily["trade_date"].min().date(),
            "end_date": daily["trade_date"].max().date(),
            "adjustment_methods": sorted(daily["adjustment_method"].dropna().unique().tolist()),
            "source": sorted(daily["source"].dropna().unique().tolist()),
            "latest_quality_report": self.store.processed_dir.joinpath(
                "history_quality_report.json"
            ).is_file(),
            "coverage": coverage.to_dict(orient="records"),
        }

    @staticmethod
    def _expected_date(
        calendar: pd.DataFrame,
        as_of: date | None,
    ) -> pd.Timestamp | None:
        if as_of is None:
            return infer_latest_completed_session(calendar)
        return infer_latest_completed_session(
            calendar,
            now=pd.Timestamp(as_of, tz="Asia/Shanghai") + pd.Timedelta(hours=16),
        )
