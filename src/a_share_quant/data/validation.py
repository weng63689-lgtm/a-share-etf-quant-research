"""Fail-fast validation for reference datasets."""

from datetime import date

import pandas as pd


def validate_reference_data(
    calendar: pd.DataFrame,
    etf_master: pd.DataFrame,
    universe: pd.DataFrame,
    *,
    today: date | None = None,
) -> list[str]:
    errors: list[str] = []
    today = today or date.today()

    if calendar.empty:
        errors.append("trading_calendar is empty")
    else:
        if calendar["calendar_date"].duplicated().any():
            errors.append("trading_calendar contains duplicate dates")
        if not calendar["calendar_date"].is_monotonic_increasing:
            errors.append("trading_calendar is not sorted")
        if calendar["is_open"].sum() < 200:
            errors.append("trading_calendar contains too few open dates")
        if pd.Timestamp(today) > calendar["calendar_date"].max():
            errors.append("trading_calendar does not cover today")

    if etf_master.empty:
        errors.append("etf_master is empty")
    else:
        if len(etf_master) < 500:
            errors.append("etf_master contains fewer than 500 rows")
        if etf_master["symbol"].duplicated().any():
            errors.append("etf_master contains duplicate symbols")
        if not etf_master["symbol"].astype(str).str.fullmatch(r"\d{6}").all():
            errors.append("etf_master contains invalid symbols")
        if not etf_master["exchange"].isin(["SSE", "SZSE"]).all():
            errors.append("etf_master contains unsupported exchanges")
        for column in ("last_price", "volume", "turnover"):
            values = pd.to_numeric(etf_master[column], errors="coerce").dropna()
            if (values < 0).any():
                errors.append(f"etf_master contains negative {column}")

    if universe.empty:
        errors.append("strategy_universe is empty")
    else:
        universe_symbols = universe["symbol"].astype(str).str.zfill(6)
        if universe_symbols.duplicated().any():
            errors.append("strategy_universe contains duplicate symbols")
        missing = sorted(set(universe_symbols) - set(etf_master["symbol"].astype(str)))
        if missing:
            errors.append(f"strategy_universe symbols missing from etf_master: {missing}")
        if not universe["exchange"].isin(["SSE", "SZSE"]).all():
            errors.append("strategy_universe contains unsupported exchanges")

    return errors

