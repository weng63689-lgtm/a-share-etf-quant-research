"""Normalize and validate unadjusted plus forward-adjusted ETF daily history."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

DAILY_COLUMNS = (
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
    "source",
    "retrieved_at",
)

_PROVIDER_COLUMNS = {
    "trade_date": "日期",
    "open": "开盘",
    "close": "收盘",
    "high": "最高",
    "low": "最低",
    "volume": "成交量",
    "turnover": "成交额",
    "amplitude_pct": "振幅",
    "change_pct": "涨跌幅",
    "change_amount": "涨跌额",
    "turnover_rate_pct": "换手率",
}


def _naive_trade_dates(values: object) -> pd.DatetimeIndex:
    dates = pd.DatetimeIndex(pd.to_datetime(values, errors="coerce"))
    dates = dates[~dates.isna()]
    if dates.tz is not None:
        dates = dates.tz_convert("Asia/Shanghai").tz_localize(None)
    return dates.normalize().drop_duplicates().sort_values()


def _provider_frame(frame: pd.DataFrame, *, label: str) -> pd.DataFrame:
    if frame.empty:
        raise ValueError(f"{label} ETF history is empty")

    missing = sorted(set(_PROVIDER_COLUMNS.values()) - set(frame.columns))
    if missing:
        raise ValueError(f"{label} ETF history is missing columns: {missing}")

    result = pd.DataFrame(index=frame.index)
    result["trade_date"] = pd.to_datetime(frame["日期"], errors="coerce").dt.normalize()
    for target, source in _PROVIDER_COLUMNS.items():
        if target != "trade_date":
            result[target] = pd.to_numeric(frame[source], errors="coerce")

    if result["trade_date"].isna().any():
        raise ValueError(f"{label} ETF history contains invalid trade dates")
    if result["trade_date"].duplicated().any():
        raise ValueError(f"{label} ETF history contains duplicate trade dates")

    price_columns = ["open", "high", "low", "close"]
    if result[price_columns].isna().any().any():
        raise ValueError(f"{label} ETF history contains missing OHLC values")
    return result.sort_values("trade_date").reset_index(drop=True)


def normalize_etf_history(
    raw: pd.DataFrame,
    adjusted: pd.DataFrame,
    *,
    symbol: str,
    exchange: str,
    source: str,
    retrieved_at: str | None = None,
) -> pd.DataFrame:
    """Merge execution prices and qfq research prices into one stable schema."""
    if not pd.Series([symbol]).str.fullmatch(r"\d{6}").iloc[0]:
        raise ValueError("ETF symbol must contain exactly six digits")
    if exchange not in {"SSE", "SZSE"}:
        raise ValueError(f"Unsupported ETF exchange: {exchange}")

    raw_frame = _provider_frame(raw, label="unadjusted").rename(
        columns={
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
        }
    )
    adjusted_frame = _provider_frame(adjusted, label="adjusted")

    raw_dates = set(raw_frame["trade_date"])
    adjusted_dates = set(adjusted_frame["trade_date"])
    if raw_dates != adjusted_dates:
        missing_adjusted = sorted(raw_dates - adjusted_dates)
        missing_raw = sorted(adjusted_dates - raw_dates)
        raise ValueError(
            "Unadjusted and adjusted history date sets differ: "
            f"missing_adjusted={missing_adjusted[:3]}, missing_raw={missing_raw[:3]}"
        )

    result = raw_frame.merge(
        adjusted_frame[
            [
                "trade_date",
                "open",
                "high",
                "low",
                "close",
            ]
        ].rename(
            columns={
                "open": "adj_open",
                "high": "adj_high",
                "low": "adj_low",
                "close": "adj_close",
            }
        ),
        on="trade_date",
        how="inner",
        validate="one_to_one",
    )
    result.insert(0, "symbol", symbol)
    result.insert(1, "exchange", exchange)
    result.insert(2, "exchange_symbol", f"{symbol}.SH" if exchange == "SSE" else f"{symbol}.SZ")
    result["adjustment_factor"] = result["adj_close"] / result["close"]
    result["adjustment_method"] = "qfq"
    result["source"] = source
    result["retrieved_at"] = retrieved_at or pd.Timestamp.now(tz="Asia/Shanghai").isoformat()
    result = result.loc[:, DAILY_COLUMNS]
    return result.sort_values(["symbol", "trade_date"]).reset_index(drop=True)


@dataclass(frozen=True)
class HistoryValidationReport:
    errors: tuple[str, ...]
    latest_expected_date: pd.Timestamp | None
    coverage: pd.DataFrame
    missing_dates: pd.DataFrame

    @property
    def status(self) -> str:
        return "ok" if not self.errors else "failed"

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "latest_expected_date": (
                self.latest_expected_date.date().isoformat()
                if self.latest_expected_date is not None
                else None
            ),
            "errors": list(self.errors),
            "coverage": self.coverage.to_dict(orient="records"),
            "missing_rows": len(self.missing_dates),
        }


def infer_latest_completed_session(
    calendar: pd.DataFrame,
    *,
    now: pd.Timestamp | None = None,
    close_grace_minutes: int = 10,
) -> pd.Timestamp | None:
    """Return the latest trading session whose close should be available."""
    if calendar.empty or "calendar_date" not in calendar or "is_open" not in calendar:
        return None

    current = now if now is not None else pd.Timestamp.now(tz="Asia/Shanghai")
    if current.tzinfo is None:
        current = current.tz_localize("Asia/Shanghai")
    else:
        current = current.tz_convert("Asia/Shanghai")

    cutoff = current.normalize() + pd.Timedelta(hours=15, minutes=close_grace_minutes)
    candidate = (
        current.normalize()
        if current >= cutoff
        else current.normalize() - pd.Timedelta(days=1)
    )

    open_dates = _naive_trade_dates(calendar.loc[calendar["is_open"], "calendar_date"])
    eligible = open_dates
    candidate = candidate.tz_localize(None)
    eligible = eligible[eligible <= candidate]
    return pd.Timestamp(eligible[-1]).normalize() if not eligible.empty else None


def _price_errors(
    frame: pd.DataFrame,
    *,
    columns: tuple[str, str, str, str],
    label: str,
    sample_symbol: str,
) -> list[str]:
    open_column, high_column, low_column, close_column = columns
    errors: list[str] = []
    values = frame[list(columns)]
    if values.isna().any().any():
        errors.append(f"{label} history contains missing OHLC values for {sample_symbol}")
    if not np.isfinite(values.fillna(np.inf).to_numpy(dtype=float)).all():
        errors.append(f"{label} history contains non-finite OHLC values for {sample_symbol}")
    if (values <= 0).any().any():
        errors.append(f"{label} history contains non-positive OHLC values for {sample_symbol}")

    upper_bound = frame[[open_column, close_column, low_column]].max(axis=1)
    lower_bound = frame[[open_column, close_column, high_column]].min(axis=1)
    if (frame[high_column] < upper_bound).any():
        errors.append(f"{label} high price violates OHLC ordering for {sample_symbol}")
    if (frame[low_column] > lower_bound).any():
        errors.append(f"{label} low price violates OHLC ordering for {sample_symbol}")
    return errors


def validate_etf_history(
    daily: pd.DataFrame,
    calendar: pd.DataFrame,
    universe: pd.DataFrame,
    *,
    latest_expected_date: pd.Timestamp | None = None,
    min_coverage_ratio: float = 0.98,
    max_missing_trade_days: int = 10,
    max_missing_gap_days: int = 10,
    max_daily_return_abs: float = 0.22,
) -> HistoryValidationReport:
    """Fail closed on structural defects while reporting calendar gaps per ETF."""
    errors: list[str] = []
    coverage_rows: list[dict[str, object]] = []
    missing_frames: list[pd.DataFrame] = []

    missing_columns = sorted(set(DAILY_COLUMNS) - set(daily.columns))
    if daily.empty:
        errors.append("ETF daily history is empty")
    if missing_columns:
        errors.append(f"ETF daily history is missing columns: {missing_columns}")
    if daily.empty or missing_columns:
        return HistoryValidationReport(
            errors=tuple(errors),
            latest_expected_date=latest_expected_date,
            coverage=pd.DataFrame(),
            missing_dates=pd.DataFrame(columns=["symbol", "trade_date", "gap_length"]),
        )

    history = daily.copy()
    history["trade_date"] = pd.to_datetime(history["trade_date"], errors="coerce").dt.normalize()
    if history["trade_date"].isna().any():
        errors.append("ETF daily history contains invalid trade dates")
    if history.duplicated(["symbol", "trade_date"]).any():
        errors.append("ETF daily history contains duplicate symbol/date rows")
    if not history.equals(history.sort_values(["symbol", "trade_date"]).reset_index(drop=True)):
        errors.append("ETF daily history is not sorted by symbol and trade_date")

    universe_symbols = set(universe["symbol"].astype(str).str.zfill(6))
    history_symbols = set(history["symbol"].astype(str).str.zfill(6))
    missing_symbols = sorted(universe_symbols - history_symbols)
    extra_symbols = sorted(history_symbols - universe_symbols)
    if missing_symbols:
        errors.append(f"ETF daily history is missing universe symbols: {missing_symbols}")
    if extra_symbols:
        errors.append(f"ETF daily history contains symbols outside the universe: {extra_symbols}")

    open_dates = _naive_trade_dates(
        calendar.loc[calendar["is_open"].astype(bool), "calendar_date"]
    )
    if latest_expected_date is not None:
        expected_dates_index = _naive_trade_dates([latest_expected_date])
        expected = expected_dates_index[0] if len(expected_dates_index) else None
    else:
        expected = infer_latest_completed_session(calendar)
    future_rows = (
        history.loc[history["trade_date"] > expected, ["symbol", "trade_date"]]
        if expected is not None
        else history.iloc[0:0]
    )
    if not future_rows.empty:
        errors.append(
            "ETF daily history contains sessions after the latest completed date: "
            f"{len(future_rows)} rows"
        )

    for symbol, frame in history.groupby("symbol", sort=True):
        frame = frame.sort_values("trade_date")
        start = frame["trade_date"].min()
        end = frame["trade_date"].max()
        comparison_end = expected if expected is not None else end
        expected_dates = open_dates[(open_dates >= start) & (open_dates <= comparison_end)]
        actual_dates = pd.DatetimeIndex(frame["trade_date"].unique())
        missing = expected_dates.difference(actual_dates)
        coverage_ratio = (
            float(1 - len(missing) / len(expected_dates)) if len(expected_dates) else 0.0
        )

        gap_lengths = _consecutive_gap_lengths(expected_dates, actual_dates)
        if not missing.empty:
            missing_frames.append(
                pd.DataFrame(
                    {
                        "symbol": symbol,
                        "trade_date": missing,
                        "gap_length": gap_lengths,
                    }
                )
            )
        coverage_rows.append(
            {
                "symbol": symbol,
                "start_date": start,
                "end_date": end,
                "expected_sessions": int(len(expected_dates)),
                "observed_sessions": int(len(actual_dates.intersection(expected_dates))),
                "missing_sessions": int(len(missing)),
                "max_gap_sessions": int(gap_lengths.max(initial=0)),
                "coverage_ratio": round(coverage_ratio, 6),
            }
        )

        if expected is None:
            errors.append(f"Unable to infer latest expected trading date for {symbol}")
        elif end < expected:
            errors.append(
                f"ETF {symbol} history ends at {end.date()}, before expected {expected.date()}"
            )
        if coverage_ratio < min_coverage_ratio:
            errors.append(f"ETF {symbol} calendar coverage {coverage_ratio:.2%} is too low")
        if len(missing) > max_missing_trade_days:
            errors.append(
                f"ETF {symbol} has {len(missing)} missing sessions, above "
                f"{max_missing_trade_days}"
            )
        if gap_lengths.size and int(gap_lengths.max()) > max_missing_gap_days:
            errors.append(
                f"ETF {symbol} has a {int(gap_lengths.max())}-session gap, above "
                f"{max_missing_gap_days}"
            )

        errors.extend(
            _price_errors(
                frame,
                columns=("open", "high", "low", "close"),
                label="unadjusted",
                sample_symbol=symbol,
            )
        )
        errors.extend(
            _price_errors(
                frame,
                columns=("adj_open", "adj_high", "adj_low", "adj_close"),
                label="adjusted",
                sample_symbol=symbol,
            )
        )
        if (frame[["volume", "turnover"]] < 0).any().any():
            errors.append(f"ETF {symbol} history contains negative volume or turnover")
        factor = frame["adjustment_factor"]
        if not np.isfinite(factor.to_numpy(dtype=float)).all() or (factor <= 0).any():
            errors.append(f"ETF {symbol} history contains invalid adjustment factors")
        daily_return = frame["adj_close"].pct_change(fill_method=None).abs()
        if (daily_return > max_daily_return_abs).any():
            errors.append(
                f"ETF {symbol} contains an adjusted close return above "
                f"{max_daily_return_abs:.1%}"
            )

    missing_dates = (
        pd.concat(missing_frames, ignore_index=True)
        if missing_frames
        else pd.DataFrame(columns=["symbol", "trade_date", "gap_length"])
    )
    return HistoryValidationReport(
        errors=tuple(dict.fromkeys(errors)),
        latest_expected_date=expected,
        coverage=pd.DataFrame(coverage_rows),
        missing_dates=missing_dates,
    )


def _consecutive_gap_lengths(
    expected_dates: pd.DatetimeIndex,
    actual_dates: pd.DatetimeIndex,
) -> np.ndarray:
    """Label every missing session with the length of its contiguous calendar gap."""
    positions = [
        position
        for position, value in enumerate(expected_dates)
        if value not in actual_dates
    ]
    lengths = np.zeros(len(positions), dtype=int)
    if not positions:
        return lengths

    run_start = 0
    for index in range(1, len(positions) + 1):
        if index == len(positions) or positions[index] != positions[index - 1] + 1:
            run_length = index - run_start
            lengths[run_start:index] = run_length
            run_start = index
    return lengths
