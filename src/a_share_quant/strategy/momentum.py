"""Rank ETF trend and risk-adjusted momentum without using future prices."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from a_share_quant.strategy.config import StrategyConfig


@dataclass(frozen=True)
class SignalBundle:
    indicators: pd.DataFrame
    rankings: pd.DataFrame
    targets: pd.DataFrame
    execution_targets: dict[pd.Timestamp, dict[str, float]]
    execution_signals: dict[pd.Timestamp, pd.Timestamp]


def compute_indicators(daily: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for symbol, frame in daily.groupby("symbol", sort=True):
        frame = frame.sort_values("trade_date").copy()
        close = frame["adj_close"].astype(float)
        returns = close.pct_change(fill_method=None)
        momentum_frames = [
            close / close.shift(days) - 1 for days in config.momentum_windows
        ]
        frame["momentum"] = pd.concat(momentum_frames, axis=1).mean(axis=1)
        trend_votes = pd.concat(
            [close > close.rolling(days).mean() for days in config.trend_windows],
            axis=1,
        )
        frame["trend_vote"] = trend_votes.mean(axis=1)
        frame["volatility"] = returns.rolling(config.volatility_days).std(ddof=0) * np.sqrt(252)
        frame["trend_ok"] = frame["trend_vote"] >= config.trend_consensus_ratio
        frame["score"] = frame["momentum"] / frame["volatility"]
        frame["eligible"] = (
            frame["trend_ok"]
            & (frame["momentum"] > 0)
            & frame["volatility"].gt(0)
            & frame["score"].replace([np.inf, -np.inf], np.nan).notna()
        )
        frame["symbol"] = str(symbol).zfill(6)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True).sort_values(["trade_date", "symbol"])


def rebalance_signal_dates(
    calendar: pd.DataFrame,
    frequency: str = "weekly",
) -> pd.DatetimeIndex:
    open_dates = pd.DatetimeIndex(
        pd.to_datetime(calendar.loc[calendar["is_open"], "calendar_date"]).sort_values()
    )
    if open_dates.empty:
        return open_dates
    period = "W-FRI" if frequency == "weekly" else "M"
    grouped = pd.Series(open_dates, index=open_dates).groupby(
        open_dates.to_period(period)
    ).max()
    return pd.DatetimeIndex(grouped.values).sort_values()


def weekly_signal_dates(calendar: pd.DataFrame) -> pd.DatetimeIndex:
    """Backward-compatible weekly helper used by older notebooks."""
    return rebalance_signal_dates(calendar, "weekly")


def _capped_inverse_volatility(
    rows: pd.DataFrame,
    config: StrategyConfig,
    *,
    target_exposure: float | None = None,
) -> dict[str, float]:
    if rows.empty:
        return {}
    inverse = 1 / rows.set_index("symbol")["volatility"].astype(float)
    exposure = config.target_exposure if target_exposure is None else target_exposure
    weights = inverse / inverse.sum() * exposure
    capped: dict[str, float] = {}
    remaining = set(weights.index)
    remaining_target = exposure
    while remaining:
        total_inverse = float(inverse.loc[list(remaining)].sum())
        proposed = {
            symbol: remaining_target * float(inverse.loc[symbol]) / total_inverse
            for symbol in remaining
        }
        over = [symbol for symbol, weight in proposed.items() if weight > config.max_single_weight]
        if not over:
            capped.update(proposed)
            break
        for symbol in over:
            capped[symbol] = config.max_single_weight
            remaining.remove(symbol)
            remaining_target -= config.max_single_weight
        if remaining_target <= 0 or not remaining:
            break
    return capped


def _capped_equal_weight(rows: pd.DataFrame, exposure: float, cap: float) -> dict[str, float]:
    if rows.empty:
        return {}
    symbols = rows["symbol"].tolist()
    remaining = set(symbols)
    weights: dict[str, float] = {}
    remaining_exposure = exposure
    while remaining:
        proposed = remaining_exposure / len(remaining)
        if proposed <= cap:
            weights.update({symbol: proposed for symbol in remaining})
            break
        for symbol in list(remaining):
            weights[symbol] = cap
            remaining.remove(symbol)
            remaining_exposure -= cap
        if remaining_exposure <= 0:
            break
    return weights


def _portfolio_weights(
    rows: pd.DataFrame,
    config: StrategyConfig,
    *,
    target_exposure: float,
    returns: pd.DataFrame,
    signal_date: pd.Timestamp,
) -> dict[str, float]:
    if config.portfolio_method == "equal_weight":
        return _capped_equal_weight(rows, target_exposure, config.max_single_weight)
    weights = _capped_inverse_volatility(
        rows,
        config,
        target_exposure=target_exposure,
    )
    if config.portfolio_method != "volatility_target" or not weights:
        return weights
    symbols = list(weights)
    covariance = (
        returns.loc[:signal_date]
        .tail(config.covariance_days)[symbols]
        .cov()
        .to_numpy(dtype=float)
        * 252
    )
    vector = np.array([weights[symbol] for symbol in symbols], dtype=float)
    portfolio_volatility = float(np.sqrt(vector @ covariance @ vector))
    if portfolio_volatility <= 0:
        return weights
    scale = min(1.0, config.target_volatility / portfolio_volatility)
    return {symbol: weight * scale for symbol, weight in weights.items()}


def build_signals(
    daily: pd.DataFrame,
    calendar: pd.DataFrame,
    config: StrategyConfig,
    benchmark: pd.DataFrame | None = None,
) -> SignalBundle:
    indicators = compute_indicators(daily, config)
    signal_dates = rebalance_signal_dates(calendar, config.rebalance_frequency)
    open_dates = pd.DatetimeIndex(
        pd.to_datetime(calendar.loc[calendar["is_open"], "calendar_date"]).sort_values()
    )
    ranking_frames: list[pd.DataFrame] = []
    target_frames: list[pd.DataFrame] = []
    execution_targets: dict[pd.Timestamp, dict[str, float]] = {}
    execution_signals: dict[pd.Timestamp, pd.Timestamp] = {}
    previous_targets: set[str] = set()
    benchmark_trend: dict[pd.Timestamp, bool] = {}
    close_returns = indicators.pivot(
        index="trade_date",
        columns="symbol",
        values="adj_close",
    ).pct_change(fill_method=None)
    if benchmark is not None and config.market_filter:
        benchmark_frame = benchmark.sort_values("trade_date").copy()
        benchmark_frame["trade_date"] = pd.to_datetime(benchmark_frame["trade_date"])
        benchmark_frame["market_ma"] = benchmark_frame["close"].rolling(
            config.benchmark_trend_days
        ).mean()
        benchmark_frame["market_up"] = (
            benchmark_frame["close"] > benchmark_frame["market_ma"]
        )
        benchmark_trend = {
            pd.Timestamp(row.trade_date).normalize(): bool(row.market_up)
            for row in benchmark_frame.dropna(subset=["market_ma"]).itertuples(index=False)
        }

    for signal_date in signal_dates:
        future_dates = open_dates[open_dates > signal_date]
        if future_dates.empty:
            continue
        execution_date = pd.Timestamp(future_dates[0]).normalize()
        market_up = benchmark_trend.get(pd.Timestamp(signal_date), True)
        exposure = (
            config.target_exposure
            if market_up or not config.market_filter
            else config.defensive_exposure
        )
        rows = indicators[
            (indicators["trade_date"] == signal_date) & indicators["eligible"]
        ].copy()
        if rows.empty:
            rankings = pd.DataFrame()
            selected_symbols = []
            weights = {}
        else:
            rows = rows.sort_values(["score", "symbol"], ascending=[False, True])
            rows["rank"] = range(1, len(rows) + 1)
            rankings = rows[
                [
                    "trade_date",
                    "symbol",
                    "rank",
                    "momentum",
                    "volatility",
                    "score",
                    "trend_ok",
                    "eligible",
                ]
            ].copy()
            rankings.insert(1, "signal_date", signal_date)
            rankings.insert(2, "execution_date", execution_date)
            rankings["retained"] = rankings["symbol"].isin(previous_targets)
            rankings["market_up"] = market_up
            rankings["market_exposure"] = exposure
            ranking_frames.append(rankings)

            strong_new = set(rows.head(config.selection_count)["symbol"])
            retained = set(
                rows[
                    rows["symbol"].isin(previous_targets)
                    & (rows["rank"] <= config.retention_rank)
                ]["symbol"]
            )
            candidates = rows[rows["symbol"].isin(strong_new | retained)]
            candidates = candidates.sort_values(["rank", "symbol"]).head(
                config.selection_count
            )
            selected_symbols = candidates["symbol"].tolist()
            weights = _portfolio_weights(
                candidates,
                config,
                target_exposure=exposure,
                returns=close_returns,
                signal_date=signal_date,
            )

        previous_targets = set(selected_symbols)
        execution_targets[execution_date] = weights
        execution_signals[execution_date] = signal_date
        for symbol, weight in weights.items():
            target_frames.append(
                {
                    "signal_date": signal_date,
                    "execution_date": execution_date,
                    "symbol": symbol,
                    "target_weight": weight,
                    "market_up": market_up,
                    "market_exposure": exposure,
                }
            )

    rankings_frame = (
        pd.concat(ranking_frames, ignore_index=True)
        if ranking_frames
        else pd.DataFrame(
            columns=[
                "signal_date",
                "execution_date",
                "trade_date",
                "symbol",
                "rank",
                "momentum",
                "volatility",
                "score",
                "trend_ok",
                "eligible",
                "retained",
                "market_up",
                "market_exposure",
            ]
        )
    )
    targets_frame = (
        pd.DataFrame(target_frames)
        if target_frames
        else pd.DataFrame(
            columns=[
                "signal_date",
                "execution_date",
                "symbol",
                "target_weight",
                "market_up",
                "market_exposure",
            ]
        )
    )
    if not targets_frame.empty:
        exposure = targets_frame.groupby("execution_date")["target_weight"].sum()
        if (exposure > config.target_exposure + 1e-12).any():
            raise ValueError("Target weights exceed configured total exposure")

    return SignalBundle(
        indicators=indicators,
        rankings=rankings_frame,
        targets=targets_frame,
        execution_targets=execution_targets,
        execution_signals=execution_signals,
    )
