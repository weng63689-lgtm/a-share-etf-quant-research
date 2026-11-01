# ruff: noqa: F821
"""
CN Domestic Multi-Asset ETF Volatility Target

QuantDinger Strategy API V2 port of the frozen domestic ETF research strategy.
The universe contains 20 Shanghai and Shenzhen-listed ETFs covering domestic
equity, bonds, gold, and money-market products. Signals use 120-day momentum,
the 200-day trend, 20-day volatility, a 60-day covariance estimate, and a 10%
annualized portfolio volatility target. Rebalance monthly, hold at most three
instruments, remain long-only, and never enable leverage.

This is a signal-only research integration. QuantDinger's China data adapter may
use forward-adjusted prices for both signals and simulated fills, so its ledger
is not a byte-for-byte replacement for the local A-share execution model.
"""

import numpy as np
import pandas as pd

# @param momentum_days int 120 Momentum lookback range=60:240:20
# @param trend_days int 200 Long-term trend filter range=120:250:10
# @param volatility_days int 20 Recent volatility lookback range=10:60:5
# @param covariance_days int 60 Covariance lookback range=30:120:10
# @param selection_count int 3 Maximum holdings range=1:5:1
# @param target_exposure float 0.90 Maximum portfolio exposure range=0.3:0.95:0.05
# @param target_volatility float 0.10 Annualized volatility target range=0.05:0.20:0.01
# @param max_single_weight float 0.35 Maximum ETF weight range=0.10:0.50:0.05

SYMBOLS = [
    "CNStock:510050.SH",
    "CNStock:510300.SH",
    "CNStock:510500.SH",
    "CNStock:512100.SH",
    "CNStock:588000.SH",
    "CNStock:159915.SZ",
    "CNStock:512890.SH",
    "CNStock:512880.SH",
    "CNStock:512800.SH",
    "CNStock:512660.SH",
    "CNStock:159928.SZ",
    "CNStock:159949.SZ",
    "CNStock:512170.SH",
    "CNStock:512480.SH",
    "CNStock:511010.SH",
    "CNStock:511220.SH",
    "CNStock:511260.SH",
    "CNStock:518880.SH",
    "CNStock:511880.SH",
    "CNStock:511990.SH",
]

DRAWDOWN_REDUCE_1 = 0.08
DRAWDOWN_REDUCE_2 = 0.12
DRAWDOWN_STOP = 0.16
DRAWDOWN_LEVEL_1_EXPOSURE = 0.70
DRAWDOWN_LEVEL_2_EXPOSURE = 0.40
DRAWDOWN_COOLDOWN_DAYS = 20
DRAWDOWN_REENTRY_EXPOSURE = 0.40
MIN_REBALANCE_TURNOVER = 0.10
MAX_REBALANCE_TURNOVER = 0.50
MIN_TRADE_WEIGHT = 0.04
RETENTION_RANK = 5
POSITION_STOP_LOSS_PCT = 0.0


def initialize(context):
    context.set_universe(SYMBOLS)
    context.subscribe(
        frequency="1d",
        fields=["open", "high", "low", "close", "volume"],
    )
    context.set_warmup(260)
    context.set_benchmark("CNStock:510300.SH")
    context.set_metadata(
        direction_mode="long_only",
        strategy_family="cn_domestic_etf_multi_asset",
        research_source="a-share-etf-quant",
    )
    run_monthly(rebalance, monthday=1, time="09:30")
    g.previous_selected = []
    g.risk_scale = 1.0
    g.risk_cooldown = 0
    g.peak_equity = 0.0


def handle_data(context, data):
    _update_drawdown_state(context)


def _update_drawdown_state(context):
    equity = float(context.portfolio.total_value or 0.0)
    if equity <= 0:
        return
    if g.peak_equity <= 0:
        g.peak_equity = equity

    if g.risk_scale == 0:
        g.risk_cooldown -= 1
        if g.risk_cooldown <= 0:
            g.risk_scale = DRAWDOWN_REENTRY_EXPOSURE
            g.risk_cooldown = 0
            g.peak_equity = equity
        return

    g.peak_equity = max(g.peak_equity, equity)
    drawdown = equity / g.peak_equity - 1.0
    if drawdown <= -DRAWDOWN_STOP:
        g.risk_scale = 0.0
        g.risk_cooldown = DRAWDOWN_COOLDOWN_DAYS
        g.peak_equity = equity
    elif drawdown <= -DRAWDOWN_REDUCE_2:
        g.risk_scale = DRAWDOWN_LEVEL_2_EXPOSURE
        g.risk_cooldown = 0
    elif drawdown <= -DRAWDOWN_REDUCE_1:
        g.risk_scale = DRAWDOWN_LEVEL_1_EXPOSURE
        g.risk_cooldown = 0
    elif drawdown >= -0.04:
        g.risk_scale = 1.0
        g.risk_cooldown = 0


def _indicator_rows(context):
    momentum_days = int(context.params.get("momentum_days", 120))
    trend_days = int(context.params.get("trend_days", 200))
    volatility_days = int(context.params.get("volatility_days", 20))
    lookback = max(momentum_days + 1, trend_days + 1, volatility_days + 1)
    rows = []

    for symbol in SYMBOLS:
        bars = get_history(lookback, "1d", "close", symbol)
        if len(bars) < lookback:
            continue
        close = bars["close"].astype(float).dropna()
        if len(close) < lookback:
            continue
        returns = close.pct_change(fill_method=None).dropna()
        if len(returns) < volatility_days:
            continue

        momentum = float(close.iloc[-1] / close.iloc[-momentum_days - 1] - 1.0)
        trend = float(close.iloc[-1] > close.tail(trend_days).mean())
        volatility = float(
            returns.tail(volatility_days).std(ddof=0) * np.sqrt(252.0)
        )
        if not np.isfinite(volatility) or volatility <= 0:
            continue
        score = momentum / volatility
        if not np.isfinite(score):
            continue
        rows.append(
            {
                "symbol": symbol,
                "momentum": momentum,
                "volatility": volatility,
                "score": score,
                "eligible": bool(trend and momentum > 0),
            }
        )
    return rows


def _capped_inverse_volatility(rows, exposure, max_single_weight):
    inverse = {row["symbol"]: 1.0 / row["volatility"] for row in rows}
    remaining = set(inverse)
    remaining_exposure = exposure
    weights = {}
    while remaining:
        total_inverse = sum(inverse[symbol] for symbol in remaining)
        if total_inverse <= 0:
            break
        proposed = {
            symbol: remaining_exposure * inverse[symbol] / total_inverse
            for symbol in remaining
        }
        over = [
            symbol for symbol, weight in proposed.items() if weight > max_single_weight
        ]
        if not over:
            weights.update(proposed)
            break
        for symbol in over:
            weights[symbol] = max_single_weight
            remaining.remove(symbol)
            remaining_exposure -= max_single_weight
        if remaining_exposure <= 0:
            break
    return weights


def _volatility_scale(symbols, weights, covariance_days, target_volatility):
    return_frames = {}
    for symbol in symbols:
        bars = get_history(covariance_days + 2, "1d", "close", symbol)
        if len(bars) < covariance_days + 2:
            return 1.0
        close = bars["close"].astype(float).dropna()
        return_frames[symbol] = close.pct_change(fill_method=None)
    returns = pd.DataFrame(return_frames).dropna(how="any")
    if len(returns) < covariance_days:
        return 1.0
    covariance = returns.tail(covariance_days).cov().to_numpy(dtype=float) * 252.0
    vector = np.array([weights[symbol] for symbol in symbols], dtype=float)
    portfolio_volatility = float(np.sqrt(vector @ covariance @ vector))
    if not np.isfinite(portfolio_volatility) or portfolio_volatility <= 0:
        return 1.0
    return min(1.0, target_volatility / portfolio_volatility)


def _target_weights(context):
    selection_count = int(context.params.get("selection_count", 3))
    target_exposure = float(context.params.get("target_exposure", 0.90))
    target_volatility = float(context.params.get("target_volatility", 0.10))
    max_single_weight = float(context.params.get("max_single_weight", 0.35))
    covariance_days = int(context.params.get("covariance_days", 60))

    rows = _indicator_rows(context)
    candidates = [row for row in rows if row["eligible"]]
    candidates.sort(key=lambda row: (-row["score"], row["symbol"]))
    g.previous_selected = [
        item.get("symbol") if isinstance(item, dict) else str(item)
        for item in g.previous_selected
    ]

    strong_new = {row["symbol"] for row in candidates[:selection_count]}
    retained = {
        row["symbol"]
        for rank, row in enumerate(candidates, start=1)
        if rank <= RETENTION_RANK and row["symbol"] in g.previous_selected
    }
    selected_rows = [
        row
        for row in candidates
        if row["symbol"] in strong_new | retained
    ][:selection_count]
    g.previous_selected = [row["symbol"] for row in selected_rows]
    if not selected_rows:
        return {}

    weights = _capped_inverse_volatility(
        selected_rows,
        target_exposure,
        max_single_weight,
    )
    scale = _volatility_scale(
        list(weights),
        weights,
        covariance_days,
        target_volatility,
    )
    return {symbol: weight * scale for symbol, weight in weights.items()}


def _current_weights(context):
    total_value = float(context.portfolio.total_value or 0.0)
    if total_value <= 0:
        return {}
    weights = {}
    for symbol, position in get_positions().items():
        if "::" in symbol:
            continue
        market_value = float(position.market_value or 0.0)
        if market_value > 0:
            weights[symbol] = market_value / total_value
    return weights


def _adjust_targets(current, target):
    symbols = set(current) | set(target)
    desired = {symbol: float(target.get(symbol, 0.0)) for symbol in symbols}
    exits = {
        symbol
        for symbol in symbols
        if current.get(symbol, 0.0) > 0 and desired[symbol] == 0
    }
    forced_turnover = 0.5 * sum(current.get(symbol, 0.0) for symbol in exits)
    adjustable = symbols - exits
    adjustable_turnover = 0.5 * sum(
        abs(desired[symbol] - current.get(symbol, 0.0)) for symbol in adjustable
    )
    if not exits and adjustable_turnover < MIN_REBALANCE_TURNOVER:
        return dict(current)

    available = max(0.0, MAX_REBALANCE_TURNOVER - forced_turnover)
    if adjustable_turnover > available:
        scale = available / adjustable_turnover if adjustable_turnover > 0 else 0.0
        for symbol in adjustable:
            desired[symbol] = current.get(symbol, 0.0) + scale * (
                desired[symbol] - current.get(symbol, 0.0)
            )

    for symbol in symbols:
        difference = abs(desired[symbol] - current.get(symbol, 0.0))
        if (
            current.get(symbol, 0.0) > 0
            and desired[symbol] > 0
            and difference < MIN_TRADE_WEIGHT
        ):
            desired[symbol] = current[symbol]
    return {symbol: weight for symbol, weight in desired.items() if weight > 0}


def rebalance(context, data):
    target = {
        symbol: weight * g.risk_scale
        for symbol, weight in _target_weights(context).items()
    }
    current = _current_weights(context)
    adjusted = _adjust_targets(current, target)
    symbols = sorted(set(current) | set(adjusted))
    stamp = context.current_dt.strftime("%Y%m%d")

    for symbol in symbols:
        desired = float(adjusted.get(symbol, 0.0))
        existing = float(current.get(symbol, 0.0))
        if desired + 1e-12 < existing:
            order_target_percent(
                symbol,
                desired,
                reason="cn_etf_monthly_reduce",
                client_order_id=f"qd-cn-etf-{stamp}-{symbol}-reduce",
            )
    for symbol in symbols:
        desired = float(adjusted.get(symbol, 0.0))
        existing = float(current.get(symbol, 0.0))
        if desired > existing + 1e-12:
            if POSITION_STOP_LOSS_PCT > 0:
                order_target_percent(
                    symbol,
                    desired,
                    reason="cn_etf_monthly_select",
                    client_order_id=f"qd-cn-etf-{stamp}-{symbol}-select",
                    stop_loss_pct=POSITION_STOP_LOSS_PCT,
                )
            else:
                order_target_percent(
                    symbol,
                    desired,
                    reason="cn_etf_monthly_select",
                    client_order_id=f"qd-cn-etf-{stamp}-{symbol}-select",
                )
