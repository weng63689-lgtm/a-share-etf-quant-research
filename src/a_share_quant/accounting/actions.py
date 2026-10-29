"""Build auditable cash-distribution and share-adjustment events."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

ACTION_COLUMNS = (
    "symbol",
    "event_date",
    "action_type",
    "cash_per_unit",
    "share_multiplier",
    "source",
    "retrieved_at",
    "derivation",
)


def empty_actions() -> pd.DataFrame:
    return pd.DataFrame(columns=ACTION_COLUMNS)


def normalize_cash_distributions(
    raw: pd.DataFrame,
    *,
    symbol: str,
    source: str,
    retrieved_at: str,
) -> pd.DataFrame:
    """Convert cumulative dividend amounts into individual ex-date events."""
    if raw.empty:
        return empty_actions()

    date_column = next((name for name in ("日期", "date") if name in raw.columns), None)
    amount_column = next(
        (name for name in ("累计分红", "cumulative_dividend") if name in raw.columns),
        None,
    )
    if date_column is None or amount_column is None:
        raise ValueError(f"ETF distribution source is missing date or amount for {symbol}")

    frame = pd.DataFrame(
        {
            "event_date": pd.to_datetime(raw[date_column], errors="coerce").dt.normalize(),
            "cumulative": pd.to_numeric(raw[amount_column], errors="coerce"),
        }
    )
    frame = frame.dropna().sort_values("event_date").drop_duplicates("event_date", keep="last")
    frame = frame[frame["event_date"] > pd.Timestamp("1900-01-01")].reset_index(drop=True)
    if frame.empty:
        return empty_actions()

    previous = frame["cumulative"].shift(1, fill_value=0.0)
    frame["cash_per_unit"] = frame["cumulative"] - previous
    frame = frame[frame["cash_per_unit"] > 0].copy()
    if frame.empty:
        return empty_actions()

    frame["symbol"] = symbol
    frame["action_type"] = "cash_distribution"
    frame["share_multiplier"] = 1.0
    frame["source"] = source
    frame["retrieved_at"] = retrieved_at
    frame["derivation"] = "sina_cumulative_distribution_difference"
    return frame.loc[:, ACTION_COLUMNS].reset_index(drop=True)


def derive_share_adjustments(
    daily: pd.DataFrame,
    cash_actions: pd.DataFrame,
    *,
    return_gap_threshold: float = 0.05,
) -> pd.DataFrame:
    """Infer non-cash unit conversions from raw and qfq return discontinuities."""
    if daily.empty:
        return empty_actions()

    cash_dates_by_symbol: dict[str, set[pd.Timestamp]] = {}
    for row in cash_actions.itertuples(index=False):
        if row.action_type == "cash_distribution":
            symbol = str(row.symbol)
            cash_dates_by_symbol.setdefault(symbol, set()).add(
                pd.Timestamp(row.event_date).normalize()
            )
    frames: list[pd.DataFrame] = []
    for symbol, frame in daily.groupby("symbol", sort=True):
        frame = frame.sort_values("trade_date").copy()
        frame["raw_return"] = frame["close"].pct_change(fill_method=None)
        frame["adjusted_return"] = frame["adj_close"].pct_change(fill_method=None)
        frame["factor_ratio"] = frame["adjustment_factor"] / frame["adjustment_factor"].shift(1)
        frame["return_gap"] = (frame["raw_return"] - frame["adjusted_return"]).abs()
        cash_dates = cash_dates_by_symbol.get(str(symbol), set())
        candidates = frame[
            (frame["return_gap"] >= return_gap_threshold)
            & ~frame["trade_date"].isin(cash_dates)
        ].copy()
        if candidates.empty:
            continue

        candidates["symbol"] = str(symbol)
        candidates["event_date"] = candidates["trade_date"].dt.normalize()
        candidates["action_type"] = "share_adjustment"
        candidates["cash_per_unit"] = 0.0
        candidates["share_multiplier"] = candidates["factor_ratio"]
        candidates["source"] = candidates["source"].astype(str)
        candidates["retrieved_at"] = candidates["retrieved_at"].astype(str)
        candidates["derivation"] = "raw_vs_qfq_share_adjustment"
        frames.append(candidates.loc[:, ACTION_COLUMNS])

    return (
        pd.concat(frames, ignore_index=True).sort_values(["symbol", "event_date"]).reset_index(
            drop=True
        )
        if frames
        else empty_actions()
    )


def combine_actions(cash_actions: pd.DataFrame, share_actions: pd.DataFrame) -> pd.DataFrame:
    frames = [frame for frame in (cash_actions, share_actions) if not frame.empty]
    if not frames:
        return empty_actions()
    actions = pd.concat(frames, ignore_index=True)
    return actions.loc[:, ACTION_COLUMNS].sort_values(["symbol", "event_date"]).reset_index(
        drop=True
    )


@dataclass(frozen=True)
class ActionValidationReport:
    errors: tuple[str, ...]

    @property
    def status(self) -> str:
        return "ok" if not self.errors else "failed"


def validate_actions(
    actions: pd.DataFrame,
    daily: pd.DataFrame,
    universe: pd.DataFrame,
) -> ActionValidationReport:
    errors: list[str] = []
    if actions.empty:
        return ActionValidationReport(errors=())

    missing_columns = sorted(set(ACTION_COLUMNS) - set(actions.columns))
    if missing_columns:
        return ActionValidationReport(
            errors=(f"Corporate actions are missing columns: {missing_columns}",)
        )
    if actions.duplicated(["symbol", "event_date", "action_type"]).any():
        errors.append("Corporate actions contain duplicate symbol/date/type rows")
    if not actions["action_type"].isin(["cash_distribution", "share_adjustment"]).all():
        errors.append("Corporate actions contain unsupported action types")
    if (pd.to_numeric(actions["cash_per_unit"], errors="coerce") < 0).any():
        errors.append("Corporate actions contain negative cash per unit")
    multipliers = pd.to_numeric(actions["share_multiplier"], errors="coerce")
    if not np.isfinite(multipliers.to_numpy(dtype=float)).all() or (multipliers <= 0).any():
        errors.append("Corporate actions contain invalid share multipliers")

    universe_symbols = set(universe["symbol"].astype(str).str.zfill(6))
    action_symbols = set(actions["symbol"].astype(str).str.zfill(6))
    unknown = sorted(action_symbols - universe_symbols)
    if unknown:
        errors.append(f"Corporate actions contain symbols outside the universe: {unknown}")

    daily_dates = {
        str(symbol): set(pd.to_datetime(frame["trade_date"]).dt.normalize())
        for symbol, frame in daily.groupby("symbol")
    }
    for row in actions.itertuples(index=False):
        symbol = str(row.symbol).zfill(6)
        event_date = pd.Timestamp(row.event_date).normalize()
        if event_date not in daily_dates.get(symbol, set()):
            errors.append(
                f"Corporate action for {symbol} is outside its daily history: "
                f"{event_date.date()}"
            )

    return ActionValidationReport(errors=tuple(dict.fromkeys(errors)))
