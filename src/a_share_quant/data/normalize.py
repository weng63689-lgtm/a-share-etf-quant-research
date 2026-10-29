"""Convert provider-specific frames to stable project schemas."""

from datetime import date
from typing import Any

import pandas as pd


def _retrieved_at() -> str:
    return pd.Timestamp.now(tz="Asia/Shanghai").isoformat()


def normalize_trade_calendar(
    raw: pd.DataFrame,
    *,
    source: str,
    start_date: str | date = "2005-01-01",
    end_date: str | date | None = None,
) -> pd.DataFrame:
    if "trade_date" not in raw.columns:
        raise ValueError("Trade calendar source is missing the trade_date column")

    open_dates = pd.to_datetime(raw["trade_date"], errors="coerce").dropna().dt.normalize()
    open_dates = pd.DatetimeIndex(open_dates.unique()).sort_values()
    if open_dates.empty:
        raise ValueError("Trade calendar source returned no valid dates")

    start = pd.Timestamp(start_date).normalize()
    available_end = open_dates.max()
    end = pd.Timestamp(end_date).normalize() if end_date is not None else available_end
    if end > available_end:
        raise ValueError(
            f"Requested calendar end {end.date()} exceeds source coverage {available_end.date()}"
        )
    if start > end:
        raise ValueError("Calendar start date must not be after end date")

    all_dates = pd.date_range(start=start, end=end, freq="D")
    frame = pd.DataFrame({"calendar_date": all_dates})
    frame["market"] = "CN_STOCK"
    frame["is_open"] = frame["calendar_date"].isin(open_dates)

    open_series = frame["calendar_date"].where(frame["is_open"])
    frame["previous_open_date"] = open_series.ffill().shift(1)
    frame["next_open_date"] = open_series.bfill().shift(-1)
    frame["source"] = source
    frame["retrieved_at"] = _retrieved_at()
    return frame


def _first_column(frame: pd.DataFrame, names: tuple[str, ...]) -> pd.Series:
    for name in names:
        if name in frame.columns:
            return frame[name]
    return pd.Series([pd.NA] * len(frame), index=frame.index, dtype="object")


def _exchange_from_raw(value: Any, symbol: str) -> str | None:
    raw = str(value).lower()
    if raw.startswith("sh"):
        return "SSE"
    if raw.startswith("sz"):
        return "SZSE"
    if symbol.startswith(("5", "6")):
        return "SSE"
    if symbol.startswith(("1", "3")):
        return "SZSE"
    return None


def normalize_etf_snapshot(raw: pd.DataFrame, *, source: str) -> pd.DataFrame:
    if raw.empty:
        raise ValueError("ETF source returned no rows")

    raw_code = _first_column(raw, ("代码", "基金代码"))
    symbol = raw_code.astype(str).str.extract(r"(\d{6})", expand=False)
    name = _first_column(raw, ("名称", "基金名称", "基金简称")).astype("string")
    retrieved_at = _retrieved_at()

    frame = pd.DataFrame(index=raw.index)
    frame["symbol"] = symbol
    frame["exchange"] = [
        _exchange_from_raw(code, sym) if isinstance(sym, str) else None
        for code, sym in zip(raw_code, symbol, strict=True)
    ]
    frame["exchange_symbol"] = frame.apply(
        lambda row: (
            f"{row['symbol']}.SH"
            if row["exchange"] == "SSE"
            else f"{row['symbol']}.SZ"
            if row["exchange"] == "SZSE"
            else pd.NA
        ),
        axis=1,
    )
    frame["name"] = name.str.strip()
    frame["security_type"] = "ETF"
    frame["lot_size"] = 100
    frame["last_price"] = pd.to_numeric(
        _first_column(raw, ("最新价", "市价")), errors="coerce"
    )
    frame["volume"] = pd.to_numeric(_first_column(raw, ("成交量",)), errors="coerce")
    frame["turnover"] = pd.to_numeric(_first_column(raw, ("成交额",)), errors="coerce")
    data_date = _first_column(raw, ("数据日期", "查询日期", "最新-交易日"))
    frame["data_date"] = pd.to_datetime(data_date, errors="coerce").dt.normalize()
    frame["source"] = source
    frame["retrieved_at"] = retrieved_at

    frame = frame.dropna(subset=["symbol", "exchange", "name"])
    frame = frame[frame["symbol"].str.fullmatch(r"\d{6}")]
    frame = frame.drop_duplicates(subset=["symbol"], keep="first")
    return frame.sort_values(["exchange", "symbol"]).reset_index(drop=True)

