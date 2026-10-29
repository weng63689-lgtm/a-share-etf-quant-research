"""Daily incremental updates for all ETFs in the frozen research universe."""

from datetime import date
from pathlib import Path

import pandas as pd

from a_share_quant.accounting.actions import (
    combine_actions,
    derive_share_adjustments,
    normalize_cash_distributions,
    validate_actions,
)
from a_share_quant.data.history import (
    infer_latest_completed_session,
    normalize_etf_history,
    validate_etf_history,
)
from a_share_quant.data.providers.akshare_provider import AkShareProvider
from a_share_quant.data.storage import DataStore


def merge_incremental_daily(
    existing: pd.DataFrame,
    recent: pd.DataFrame,
    *,
    adjustment_tolerance: float = 5e-4,
) -> pd.DataFrame:
    """Append a verified overlap; return an empty frame when a full refetch is needed."""
    existing = existing.copy()
    recent = recent.copy()
    existing["trade_date"] = pd.to_datetime(existing["trade_date"]).dt.normalize()
    recent["trade_date"] = pd.to_datetime(recent["trade_date"]).dt.normalize()
    overlap = existing.merge(
        recent,
        on="trade_date",
        suffixes=("_old", "_new"),
        how="inner",
        validate="one_to_one",
    )
    if overlap.empty:
        return pd.DataFrame()
    raw_close_difference = (
        overlap["close_old"] - overlap["close_new"]
    ).abs() / overlap["close_old"].abs().clip(lower=1e-12)
    if (raw_close_difference > 1e-7).any():
        raise ValueError("Overlapping unadjusted closes changed; possible vendor revision")

    ratios = overlap["adj_close_new"] / overlap["adj_close_old"]
    median_ratio = float(ratios.median())
    relative_deviation = (ratios / median_ratio - 1).abs()
    if (
        abs(median_ratio - 1) > adjustment_tolerance
        or (relative_deviation > adjustment_tolerance).any()
    ):
        return pd.DataFrame()

    boundary = recent["trade_date"].min()
    older = existing[existing["trade_date"] < boundary]
    combined = pd.concat([older, recent], ignore_index=True)
    return (
        combined.drop_duplicates(["symbol", "trade_date"], keep="last")
        .sort_values(["symbol", "trade_date"])
        .reset_index(drop=True)
    )


class DailyDataUpdater:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.store = DataStore(project_root)
        self.provider = AkShareProvider()

    def update(self, *, as_of: date | None = None) -> dict[str, object]:
        calendar = self.store.load_processed("trading_calendar")
        universe = self.store.load_universe("research_universe_v3.csv")
        existing = self.store.load_processed("multi_asset_research_etf_daily")
        target_date = (
            pd.Timestamp(as_of).normalize()
            if as_of is not None
            else infer_latest_completed_session(calendar)
        )
        if target_date is None:
            raise ValueError("Unable to determine latest completed trading session")
        latest_stored = pd.to_datetime(existing["trade_date"]).max()
        if latest_stored >= target_date:
            return {
                "status": "already_current",
                "symbols": int(existing["symbol"].nunique()),
                "latest_date": latest_stored.date(),
                "target_date": target_date.date(),
            }

        updated_frames: list[pd.DataFrame] = []
        refetched_full: list[str] = []
        cash_frames: list[pd.DataFrame] = []
        for row in universe.itertuples(index=False):
            symbol = str(row.symbol).zfill(6)
            symbol_frame = existing[existing["symbol"] == symbol].sort_values("trade_date")
            symbol_latest = pd.to_datetime(symbol_frame["trade_date"]).max()
            recent_start = max(
                symbol_latest - pd.Timedelta(days=10),
                pd.Timestamp(symbol_frame["trade_date"].min()),
            ).date()
            recent = self.provider.fetch_etf_daily(
                symbol,
                str(row.exchange),
                start_date=recent_start,
                end_date=target_date.date(),
                prefer_tencent=True,
            )
            self.store.save_raw(
                recent.raw,
                f"daily_increment_raw_{symbol}",
                recent.source,
            )
            self.store.save_raw(
                recent.adjusted,
                f"daily_increment_qfq_{symbol}",
                recent.source,
            )
            recent_normalized = normalize_etf_history(
                recent.raw,
                recent.adjusted,
                symbol=symbol,
                exchange=str(row.exchange),
                source=recent.source,
                retrieved_at=recent.retrieved_at,
            )
            merged = merge_incremental_daily(symbol_frame, recent_normalized)
            if merged.empty:
                full_start = pd.Timestamp(symbol_frame["trade_date"].min()).date()
                full = self.provider.fetch_etf_daily(
                    symbol,
                    str(row.exchange),
                    start_date=full_start,
                    end_date=target_date.date(),
                    prefer_tencent=True,
                )
                self.store.save_raw(
                    full.raw,
                    f"daily_full_refresh_raw_{symbol}",
                    full.source,
                )
                self.store.save_raw(
                    full.adjusted,
                    f"daily_full_refresh_qfq_{symbol}",
                    full.source,
                )
                merged = normalize_etf_history(
                    full.raw,
                    full.adjusted,
                    symbol=symbol,
                    exchange=str(row.exchange),
                    source=full.source,
                    retrieved_at=full.retrieved_at,
                )
                refetched_full.append(symbol)
            updated_frames.append(merged)

            dividend = self.provider.fetch_etf_dividends(symbol, str(row.exchange))
            cash = normalize_cash_distributions(
                dividend.frame,
                symbol=symbol,
                source=dividend.source,
                retrieved_at=recent.retrieved_at,
            )
            if not cash.empty:
                cash_frames.append(cash)

        daily = (
            pd.concat(updated_frames, ignore_index=True)
            .sort_values(["symbol", "trade_date"])
            .reset_index(drop=True)
        )
        report = validate_etf_history(
            daily,
            calendar,
            universe,
            latest_expected_date=target_date,
        )
        if report.errors:
            raise ValueError("Incremental history validation failed: " + "; ".join(report.errors))
        cash_actions = pd.concat(cash_frames, ignore_index=True)
        actions = combine_actions(
            cash_actions,
            derive_share_adjustments(daily, cash_actions),
        )
        action_report = validate_actions(actions, daily, universe)
        if action_report.errors:
            raise ValueError(
                "Incremental corporate-action validation failed: "
                + "; ".join(action_report.errors)
            )

        daily_path = self.store.save_processed(daily, "multi_asset_research_etf_daily")
        action_path = self.store.save_processed(
            actions,
            "multi_asset_research_etf_corporate_actions",
        )
        self.store.save_processed(
            report.coverage,
            "multi_asset_research_etf_daily_coverage",
        )
        self.store.save_processed(
            report.missing_dates,
            "multi_asset_research_etf_daily_missing_dates",
        )
        self.store.update_research_database(
            daily,
            actions,
            prefix="multi_asset_",
        )
        payload = {
            "status": "ok",
            "updated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "target_date": target_date.date(),
            "symbols": int(daily["symbol"].nunique()),
            "rows": len(daily),
            "full_refetched_symbols": refetched_full,
            "actions": len(actions),
            "missing_rows": len(report.missing_dates),
            "daily_path": str(daily_path),
            "action_path": str(action_path),
        }
        self.store.save_daily_update_report(payload)
        return payload
