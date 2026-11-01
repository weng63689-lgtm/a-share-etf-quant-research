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
from a_share_quant.data.providers.a_stock_data_provider import AStockDataProvider
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

    new_rows = recent[recent["trade_date"] > existing["trade_date"].max()]
    combined = pd.concat([existing, new_rows], ignore_index=True)
    return (
        combined.drop_duplicates(["symbol", "trade_date"], keep="last")
        .sort_values(["symbol", "trade_date"])
        .reset_index(drop=True)
    )


def filter_actions_to_daily_range(
    actions: pd.DataFrame,
    daily: pd.DataFrame,
) -> pd.DataFrame:
    """Drop vendor actions outside each symbol's stored daily-history range."""
    if actions.empty:
        return actions.copy()

    daily_dates = pd.to_datetime(daily["trade_date"]).dt.normalize()
    bounds = (
        daily.assign(trade_date=daily_dates)
        .groupby("symbol")["trade_date"]
        .agg(["min", "max"])
    )
    normalized_symbols = actions["symbol"].astype(str).str.zfill(6)
    event_dates = pd.to_datetime(actions["event_date"]).dt.normalize()
    start_dates = normalized_symbols.map(bounds["min"])
    end_dates = normalized_symbols.map(bounds["max"])
    keep = start_dates.isna() | (
        (event_dates >= start_dates) & (event_dates <= end_dates)
    )
    return actions.loc[keep].reset_index(drop=True)


def apply_cross_checked_prices(
    primary: pd.DataFrame,
    reference: pd.DataFrame,
    *,
    price_tolerance: float = 5e-4,
    minimum_coverage: float = 0.95,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Overlay independently fetched prices only after overlap validation."""
    primary_frame = primary.copy()
    reference_frame = reference.copy()
    primary_frame["trade_date"] = pd.to_datetime(primary_frame["trade_date"]).dt.normalize()
    reference_frame["trade_date"] = pd.to_datetime(reference_frame["trade_date"]).dt.normalize()
    reference_columns = [
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "adj_open",
        "adj_high",
        "adj_low",
        "adj_close",
    ]
    merged = primary_frame.merge(
        reference_frame.loc[:, reference_columns],
        on="trade_date",
        how="left",
        suffixes=("", "_reference"),
        validate="one_to_one",
    )
    matched = merged["close_reference"].notna()
    coverage = float(matched.mean()) if len(merged) else 0.0
    if coverage < minimum_coverage:
        raise ValueError(
            f"A-Stock-Data price coverage {coverage:.1%} is below "
            f"{minimum_coverage:.1%}"
        )
    if coverage == 0:
        raise ValueError("A-Stock-Data price cross-check has no overlapping dates")

    price_pairs = (
        ("open", "open_reference"),
        ("high", "high_reference"),
        ("low", "low_reference"),
        ("close", "close_reference"),
        ("adj_open", "adj_open_reference"),
        ("adj_high", "adj_high_reference"),
        ("adj_low", "adj_low_reference"),
        ("adj_close", "adj_close_reference"),
    )
    max_differences: dict[str, float] = {}
    for primary_column, reference_column in price_pairs:
        differences = (
            merged.loc[matched, primary_column] - merged.loc[matched, reference_column]
        ).abs() / merged.loc[matched, primary_column].abs().clip(lower=1e-12)
        maximum = float(differences.max())
        max_differences[primary_column] = maximum
        if maximum > price_tolerance:
            raise ValueError(
                f"A-Stock-Data price disagreement for {primary_column}: "
                f"{maximum:.6%} exceeds {price_tolerance:.4%}"
            )

    for primary_column, reference_column in price_pairs:
        primary_frame.loc[:, primary_column] = merged[reference_column].to_numpy()
    primary_frame["adjustment_factor"] = primary_frame["adj_close"] / primary_frame["close"]
    primary_frame["adjustment_method"] = "a-stock-data-tencent-qfq"
    primary_source = str(primary_frame["source"].iloc[0])
    primary_frame["source"] = f"a-stock-data.tencent_kline+{primary_source}"
    primary_frame["retrieved_at"] = pd.Timestamp.now(tz="Asia/Shanghai").isoformat()
    return primary_frame, {
        "status": "ok",
        "coverage": coverage,
        "maximum_relative_differences": max_differences,
    }


class DailyDataUpdater:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.store = DataStore(project_root)
        self.provider = AkShareProvider()
        self.price_provider = AStockDataProvider()

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
        price_crosschecks: list[dict[str, object]] = []
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
            try:
                a_stock_data = self.price_provider.fetch_etf_daily(
                    symbol,
                    str(row.exchange),
                    start_date=recent_start,
                    end_date=target_date.date(),
                )
                reference_normalized = normalize_etf_history(
                    a_stock_data.raw,
                    a_stock_data.adjusted,
                    symbol=symbol,
                    exchange=str(row.exchange),
                    source=a_stock_data.source,
                    retrieved_at=a_stock_data.retrieved_at,
                )
                recent_normalized, crosscheck = apply_cross_checked_prices(
                    recent_normalized,
                    reference_normalized,
                )
                self.store.save_raw(
                    a_stock_data.raw,
                    f"a_stock_data_price_raw_{symbol}",
                    a_stock_data.source,
                    namespace="a-stock-data",
                )
                self.store.save_raw(
                    a_stock_data.adjusted,
                    f"a_stock_data_price_qfq_{symbol}",
                    a_stock_data.source,
                    namespace="a-stock-data",
                )
                price_crosschecks.append({"symbol": symbol, **crosscheck})
            except Exception as error:
                price_crosschecks.append(
                    {
                        "symbol": symbol,
                        "status": "fallback",
                        "reason": str(error)[:500],
                        "fallback_source": recent.source,
                    }
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
            cash = filter_actions_to_daily_range(cash, merged)
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
        verified_symbols = [
            str(item["symbol"])
            for item in price_crosschecks
            if item.get("status") == "ok"
        ]
        fallback_symbols = [
            str(item["symbol"])
            for item in price_crosschecks
            if item.get("status") != "ok"
        ]
        payload = {
            "status": "ok",
            "updated_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "target_date": target_date.date(),
            "symbols": int(daily["symbol"].nunique()),
            "rows": len(daily),
            "full_refetched_symbols": refetched_full,
            "actions": len(actions),
            "missing_rows": len(report.missing_dates),
            "price_source": {
                "provider": "A-Stock-Data Tencent K-line",
                "status": "verified" if not fallback_symbols else "partial_fallback",
                "verified_symbols": verified_symbols,
                "fallback_symbols": fallback_symbols,
            },
            "price_crosschecks": price_crosschecks,
            "daily_path": str(daily_path),
            "action_path": str(action_path),
        }
        self.store.save_daily_update_report(payload)
        return payload

    def crosscheck_prices(
        self,
        *,
        days: int = 10,
        as_of: date | None = None,
    ) -> dict[str, object]:
        """Audit all ETF prices against A-Stock-Data without publishing new data."""
        if days <= 0:
            raise ValueError("days must be positive")
        universe = self.store.load_universe("research_universe_v3.csv")
        existing = self.store.load_processed("multi_asset_research_etf_daily")
        end_date = (
            pd.Timestamp(as_of).normalize()
            if as_of is not None
            else pd.to_datetime(existing["trade_date"]).max().normalize()
        )
        start_date = end_date - pd.Timedelta(days=days)
        checks: list[dict[str, object]] = []
        for row in universe.itertuples(index=False):
            symbol = str(row.symbol).zfill(6)
            primary = existing[
                (existing["symbol"].astype(str).str.zfill(6) == symbol)
                & (pd.to_datetime(existing["trade_date"]) >= start_date)
                & (pd.to_datetime(existing["trade_date"]) <= end_date)
            ]
            if primary.empty:
                checks.append(
                    {
                        "symbol": symbol,
                        "status": "failed",
                        "reason": "No stored primary prices in requested range",
                    }
                )
                continue
            try:
                reference = self.price_provider.fetch_etf_daily(
                    symbol,
                    str(row.exchange),
                    start_date=start_date.date(),
                    end_date=end_date.date(),
                )
                normalized = normalize_etf_history(
                    reference.raw,
                    reference.adjusted,
                    symbol=symbol,
                    exchange=str(row.exchange),
                    source=reference.source,
                    retrieved_at=reference.retrieved_at,
                )
                _, result = apply_cross_checked_prices(primary, normalized)
                self.store.save_raw(
                    reference.raw,
                    f"a_stock_data_audit_raw_{symbol}",
                    reference.source,
                    namespace="a-stock-data",
                )
                self.store.save_raw(
                    reference.adjusted,
                    f"a_stock_data_audit_qfq_{symbol}",
                    reference.source,
                    namespace="a-stock-data",
                )
                checks.append({"symbol": symbol, **result})
            except Exception as error:
                checks.append(
                    {
                        "symbol": symbol,
                        "status": "failed",
                        "reason": str(error)[:500],
                    }
                )
        failed = [item for item in checks if item["status"] != "ok"]
        payload = {
            "status": "ok" if not failed else "failed",
            "checked_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "provider": "A-Stock-Data Tencent K-line",
            "start_date": start_date.date(),
            "end_date": end_date.date(),
            "symbols_checked": len(checks),
            "verified_symbols": len(checks) - len(failed),
            "failed_symbols": len(failed),
            "checks": checks,
        }
        self.store.save_price_source_report(payload)
        return payload
