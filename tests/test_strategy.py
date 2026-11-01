from dataclasses import replace
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from a_share_quant.accounting.config import AccountConfig
from a_share_quant.backtest.engine import (
    StrategyBacktestEngine,
    _adjust_target_weights,
    _update_risk_state,
)
from a_share_quant.execution.costs import CostConfig, CostModel
from a_share_quant.execution.rules import TradingRule, TradingRuleBook
from a_share_quant.strategy.config import StrategyConfig
from a_share_quant.strategy.momentum import (
    SignalBundle,
    _portfolio_weights,
    build_signals,
    compute_indicators,
    rebalance_signal_dates,
    weekly_signal_dates,
)


def _config() -> StrategyConfig:
    return StrategyConfig(
        momentum_days=3,
        trend_days=5,
        volatility_days=3,
        selection_count=2,
        retention_rank=3,
        target_exposure=0.90,
        max_single_weight=0.50,
        min_trade_weight=0.025,
        min_rebalance_turnover=0.05,
        max_rebalance_turnover=0.50,
        drawdown_reduce_1=0.08,
        drawdown_reduce_2=0.12,
        drawdown_stop=0.16,
        drawdown_level_1_exposure=0.70,
        drawdown_level_2_exposure=0.40,
        drawdown_cooldown_days=20,
        drawdown_reentry_exposure=0.40,
    )


def _daily(periods: int = 220) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=periods)
    frames = []
    for symbol, slope in (("510300", 0.001), ("510500", 0.0005)):
        close = pd.Series([1 + slope * index for index in range(periods)])
        frames.append(
            pd.DataFrame(
                {
                    "symbol": symbol,
                    "trade_date": dates,
                    "adj_close": close,
                    "close": close,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def test_strategy_does_not_use_future_prices() -> None:
    config = _config()
    daily = _daily()
    cutoff = daily["trade_date"].sort_values().unique()[100]
    original = compute_indicators(daily, config)
    changed = daily.copy()
    changed.loc[changed["trade_date"] > cutoff, "adj_close"] *= 5
    repriced = compute_indicators(changed, config)

    left = original[(original["trade_date"] <= cutoff) & (original["symbol"] == "510300")]
    right = repriced[(repriced["trade_date"] <= cutoff) & (repriced["symbol"] == "510300")]
    assert left["score"].fillna(-1).tolist() == pytest.approx(
        right["score"].fillna(-1).tolist()
    )


def test_signal_weights_respect_exposure_and_single_etf_cap() -> None:
    config = _config()
    daily = _daily()
    calendar = pd.DataFrame(
        {"calendar_date": sorted(daily["trade_date"].unique()), "is_open": True}
    )
    bundle = build_signals(daily, calendar, config)

    assert not bundle.targets.empty
    exposure = bundle.targets.groupby("execution_date")["target_weight"].sum()
    assert (exposure <= config.target_exposure + 1e-12).all()
    assert (bundle.targets["target_weight"] <= config.max_single_weight + 1e-12).all()


def test_weekly_signal_dates_choose_last_open_day() -> None:
    calendar = pd.DataFrame(
        {
            "calendar_date": pd.to_datetime(
                [
                    "2026-01-05",
                    "2026-01-07",
                    "2026-01-09",
                    "2026-01-12",
                    "2026-01-13",
                ]
            ),
            "is_open": True,
        }
    )

    dates = weekly_signal_dates(calendar)

    assert pd.Timestamp("2026-01-09") in dates
    assert pd.Timestamp("2026-01-05") not in dates


def test_monthly_signal_dates_choose_month_end() -> None:
    dates = pd.bdate_range("2026-01-01", "2026-03-31")
    calendar = pd.DataFrame({"calendar_date": dates, "is_open": True})

    signals = rebalance_signal_dates(calendar, "monthly")

    assert signals.tolist() == [
        pd.Timestamp("2026-01-30"),
        pd.Timestamp("2026-02-27"),
        pd.Timestamp("2026-03-31"),
    ]


def test_market_filter_reduces_exposure_when_benchmark_is_below_trend() -> None:
    config = replace(
        _config(),
        rebalance_frequency="monthly",
        market_filter=True,
        benchmark_trend_days=20,
        defensive_exposure=0.30,
    )
    daily = _daily()
    calendar = pd.DataFrame(
        {"calendar_date": sorted(daily["trade_date"].unique()), "is_open": True}
    )
    benchmark = pd.DataFrame(
        {
            "trade_date": sorted(daily["trade_date"].unique()),
            "close": np.linspace(4000, 3000, daily["trade_date"].nunique()),
        }
    )

    bundle = build_signals(daily, calendar, config, benchmark=benchmark)

    assert not bundle.targets.empty
    assert set(bundle.targets["market_exposure"]) == {0.30}


def test_equal_weight_and_volatility_target_budgets() -> None:
    rows = pd.DataFrame(
        {
            "symbol": ["510300", "510500"],
            "volatility": [0.20, 0.30],
        }
    )
    dates = pd.bdate_range("2026-01-01", periods=80)
    returns = pd.DataFrame(
        {
            "510300": np.sin(np.arange(80)) * 0.02,
            "510500": np.cos(np.arange(80)) * 0.03,
        },
        index=dates,
    )
    equal = _portfolio_weights(
        rows,
        replace(_config(), portfolio_method="equal_weight"),
        target_exposure=0.90,
        returns=returns,
        signal_date=dates[-1],
    )
    vol_target = _portfolio_weights(
        rows,
        replace(
            _config(),
            portfolio_method="volatility_target",
            target_volatility=0.05,
        ),
        target_exposure=0.90,
        returns=returns,
        signal_date=dates[-1],
    )

    assert sum(equal.values()) == pytest.approx(0.90)
    assert sum(vol_target.values()) < 0.90


def test_backtest_engine_uses_next_session_signal(tmp_path) -> None:
    dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
    daily = pd.DataFrame(
        {
            "symbol": "510300",
            "trade_date": dates,
            "open": [10.0, 10.0, 10.2],
            "high": [10.2, 10.4, 10.5],
            "low": [9.8, 9.9, 10.0],
            "close": [10.0, 10.3, 10.4],
            "turnover": [1_000_000, 1_000_000, 1_000_000],
            "volume": [1000, 1000, 1000],
        }
    )
    calendar = pd.DataFrame({"calendar_date": dates, "is_open": True})
    benchmark = pd.DataFrame({"trade_date": dates, "close": [4000.0, 4020.0, 4040.0]})
    rule = TradingRule(
        symbol="510300",
        effective_from=pd.Timestamp("2020-01-01"),
        effective_to=None,
        price_limit_pct=Decimal("0.10"),
        tick_size=Decimal("0.001"),
        buy_lot_size=100,
        sell_lot_size=100,
        settlement_days=1,
    )
    cost_model = CostModel(
        CostConfig(
            commission_rate=Decimal("0"),
            minimum_commission=Decimal("0"),
            stamp_duty_rate=Decimal("0"),
            transfer_fee_rate=Decimal("0"),
            base_slippage_bps=Decimal("0"),
            impact_coefficient_bps=Decimal("0"),
            impact_reference_participation=Decimal("0.01"),
            impact_exponent=Decimal("0.5"),
            max_participation=Decimal("0.10"),
            stress_multipliers=(Decimal("1"),),
        )
    )
    signal_bundle = SignalBundle(
        indicators=pd.DataFrame(),
        rankings=pd.DataFrame(),
        targets=pd.DataFrame(),
        execution_targets={dates[1]: {"510300": 0.5}},
        execution_signals={dates[1]: dates[0]},
    )
    engine = StrategyBacktestEngine(
        tmp_path,
        strategy_config=StrategyConfig(
            momentum_days=3,
            trend_days=5,
            volatility_days=3,
            selection_count=2,
            retention_rank=3,
            target_exposure=0.9,
            max_single_weight=0.5,
            min_trade_weight=0.025,
            min_rebalance_turnover=0.05,
            max_rebalance_turnover=0.5,
            drawdown_reduce_1=0.08,
            drawdown_reduce_2=0.12,
            drawdown_stop=0.16,
            drawdown_level_1_exposure=0.70,
            drawdown_level_2_exposure=0.40,
            drawdown_cooldown_days=20,
            drawdown_reentry_exposure=0.40,
        ),
        account_config=AccountConfig(
            initial_cash=10000,
            lot_size=100,
            share_adjustment_return_gap=0.05,
        ),
    )

    result = engine.run(
        daily=daily,
        actions=pd.DataFrame(
            columns=[
                "symbol",
                "event_date",
                "action_type",
                "cash_per_unit",
                "share_multiplier",
                "derivation",
            ]
        ),
        calendar=calendar,
        benchmark=benchmark,
        signal_bundle=signal_bundle,
        rule_book=TradingRuleBook([rule]),
        cost_model=cost_model,
        scenario="test",
        stress_multiplier=Decimal("1"),
        with_costs=False,
        start_date=dates[0],
    )

    assert result.orders.iloc[0]["signal_date"] == dates[0]
    assert result.orders.iloc[0]["execution_date"] == dates[1]
    assert result.orders.iloc[0]["status"] == "FILLED"
    assert result.snapshots.iloc[0]["trade_date"] == dates[0]
    assert result.snapshots["reconciled"].all()


def test_backtest_engine_exits_after_confirmed_position_stop(tmp_path) -> None:
    dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
    daily = pd.DataFrame(
        {
            "symbol": "510300",
            "trade_date": dates,
            "open": [10.0, 10.0, 8.9],
            "high": [10.2, 10.1, 9.0],
            "low": [9.8, 8.8, 8.7],
            "close": [10.0, 9.0, 8.8],
            "turnover": [1_000_000, 1_000_000, 1_000_000],
            "volume": [1000, 1000, 1000],
        }
    )
    calendar = pd.DataFrame({"calendar_date": dates, "is_open": True})
    benchmark = pd.DataFrame({"trade_date": dates, "close": [4000.0, 3990.0, 3970.0]})
    rule = TradingRule(
        symbol="510300",
        effective_from=pd.Timestamp("2020-01-01"),
        effective_to=None,
        price_limit_pct=Decimal("0.10"),
        tick_size=Decimal("0.001"),
        buy_lot_size=100,
        sell_lot_size=100,
        settlement_days=1,
    )
    cost_model = CostModel(
        CostConfig(
            commission_rate=Decimal("0"),
            minimum_commission=Decimal("0"),
            stamp_duty_rate=Decimal("0"),
            transfer_fee_rate=Decimal("0"),
            base_slippage_bps=Decimal("0"),
            impact_coefficient_bps=Decimal("0"),
            impact_reference_participation=Decimal("0.01"),
            impact_exponent=Decimal("0.5"),
            max_participation=Decimal("0.10"),
            stress_multipliers=(Decimal("1"),),
        )
    )
    signal_bundle = SignalBundle(
        indicators=pd.DataFrame(),
        rankings=pd.DataFrame(),
        targets=pd.DataFrame(),
        execution_targets={dates[1]: {"510300": 0.5}},
        execution_signals={dates[1]: dates[0]},
    )
    config = replace(_config(), position_stop_loss_pct=0.08)
    engine = StrategyBacktestEngine(
        tmp_path,
        strategy_config=config,
        account_config=AccountConfig(
            initial_cash=10000,
            lot_size=100,
            share_adjustment_return_gap=0.05,
        ),
    )

    result = engine.run(
        daily=daily,
        actions=pd.DataFrame(
            columns=[
                "symbol",
                "event_date",
                "action_type",
                "cash_per_unit",
                "share_multiplier",
                "derivation",
            ]
        ),
        calendar=calendar,
        benchmark=benchmark,
        signal_bundle=signal_bundle,
        rule_book=TradingRuleBook([rule]),
        cost_model=cost_model,
        scenario="stop-test",
        stress_multiplier=Decimal("1"),
        with_costs=False,
        start_date=dates[0],
    )

    assert len(result.orders) == 2
    assert result.orders.iloc[1]["side"] == "SELL"
    assert result.orders.iloc[1]["execution_date"] == dates[2]
    assert result.orders.iloc[1]["reason"] == "POSITION_STOP_LOSS"
    assert result.snapshots.iloc[-1]["holding_count"] == 0
    assert result.snapshots["reconciled"].all()


def test_backtest_engine_trailing_stop_requires_activation(tmp_path) -> None:
    dates = pd.to_datetime(
        ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]
    )
    daily = pd.DataFrame(
        {
            "symbol": "510300",
            "trade_date": dates,
            "open": [10.0, 10.0, 10.4, 9.9],
            "high": [10.2, 10.7, 10.5, 10.0],
            "low": [9.8, 9.9, 9.8, 9.7],
            "close": [10.0, 10.6, 9.95, 9.8],
            "turnover": [1_000_000] * 4,
            "volume": [1000] * 4,
        }
    )
    calendar = pd.DataFrame({"calendar_date": dates, "is_open": True})
    benchmark = pd.DataFrame(
        {"trade_date": dates, "close": [4000.0, 4010.0, 3990.0, 3980.0]}
    )
    rule = TradingRule(
        symbol="510300",
        effective_from=pd.Timestamp("2020-01-01"),
        effective_to=None,
        price_limit_pct=Decimal("0.10"),
        tick_size=Decimal("0.001"),
        buy_lot_size=100,
        sell_lot_size=100,
        settlement_days=1,
    )
    cost_model = CostModel(
        CostConfig(
            commission_rate=Decimal("0"),
            minimum_commission=Decimal("0"),
            stamp_duty_rate=Decimal("0"),
            transfer_fee_rate=Decimal("0"),
            base_slippage_bps=Decimal("0"),
            impact_coefficient_bps=Decimal("0"),
            impact_reference_participation=Decimal("0.01"),
            impact_exponent=Decimal("0.5"),
            max_participation=Decimal("0.10"),
            stress_multipliers=(Decimal("1"),),
        )
    )
    signal_bundle = SignalBundle(
        indicators=pd.DataFrame(),
        rankings=pd.DataFrame(),
        targets=pd.DataFrame(),
        execution_targets={dates[1]: {"510300": 0.5}},
        execution_signals={dates[1]: dates[0]},
    )
    config = replace(
        _config(),
        trailing_stop_pct=0.06,
        trailing_activation_pct=0.05,
    )
    engine = StrategyBacktestEngine(
        tmp_path,
        strategy_config=config,
        account_config=AccountConfig(
            initial_cash=10000,
            lot_size=100,
            share_adjustment_return_gap=0.05,
        ),
    )

    result = engine.run(
        daily=daily,
        actions=pd.DataFrame(
            columns=[
                "symbol",
                "event_date",
                "action_type",
                "cash_per_unit",
                "share_multiplier",
                "derivation",
            ]
        ),
        calendar=calendar,
        benchmark=benchmark,
        signal_bundle=signal_bundle,
        rule_book=TradingRuleBook([rule]),
        cost_model=cost_model,
        scenario="trailing-stop-test",
        stress_multiplier=Decimal("1"),
        with_costs=False,
        start_date=dates[0],
    )

    assert len(result.orders) == 2
    assert result.orders.iloc[1]["reason"] == "POSITION_TRAILING_STOP"
    assert result.orders.iloc[1]["execution_date"] == dates[3]
    assert result.snapshots.iloc[-1]["holding_count"] == 0


def test_drawdown_control_reenters_after_cooldown() -> None:
    config = _config()
    risk_scale, cooldown, peak = _update_risk_state(
        1.0,
        0,
        100_000,
        83_000,
        config,
    )
    assert risk_scale == 0
    assert cooldown == 20

    for _ in range(19):
        risk_scale, cooldown, peak = _update_risk_state(
            risk_scale,
            cooldown,
            peak,
            83_000,
            config,
        )
    assert risk_scale == 0
    risk_scale, cooldown, peak = _update_risk_state(
        risk_scale,
        cooldown,
        peak,
        83_000,
        config,
    )
    assert risk_scale == 0.40
    assert cooldown == 0


def test_unselected_position_is_exited_despite_small_weight() -> None:
    target = _adjust_target_weights(
        {"510300": 0.02, "510500": 0.30},
        {"510500": 0.30},
        _config(),
    )

    assert "510300" not in target
    assert target["510500"] == pytest.approx(0.30)
