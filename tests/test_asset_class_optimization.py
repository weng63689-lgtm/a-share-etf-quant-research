from dataclasses import replace

import numpy as np
import pandas as pd

from a_share_quant.backtest.asset_class_optimization import (
    BOND,
    CORE,
    EQUITY,
    GOLD,
    MONEY,
    SATELLITE,
    AllocationVariant,
    CoreSatelliteVariant,
    build_asset_class_signals,
    build_core_satellite_signals,
)
from a_share_quant.strategy.config import StrategyConfig


def _config() -> StrategyConfig:
    return StrategyConfig(
        momentum_days=20,
        trend_days=50,
        volatility_days=10,
        selection_count=3,
        retention_rank=5,
        target_exposure=0.90,
        max_single_weight=0.35,
        min_trade_weight=0.04,
        min_rebalance_turnover=0.10,
        max_rebalance_turnover=0.50,
        drawdown_reduce_1=0.08,
        drawdown_reduce_2=0.12,
        drawdown_stop=0.16,
        drawdown_level_1_exposure=0.70,
        drawdown_level_2_exposure=0.40,
        drawdown_cooldown_days=20,
        drawdown_reentry_exposure=0.40,
        momentum_windows=(20,),
        trend_windows=(50,),
        trend_consensus_ratio=1.0,
        rebalance_frequency="monthly",
        portfolio_method="volatility_target",
        target_volatility=0.10,
        covariance_days=20,
    )


def test_asset_class_signals_are_point_in_time_and_fully_invested_within_limit() -> None:
    dates = pd.bdate_range("2024-01-01", periods=260)
    symbols = {
        "510300": EQUITY,
        "511010": BOND,
        "518880": GOLD,
        "511880": MONEY,
    }
    daily_rows = []
    for offset, (symbol, _) in enumerate(symbols.items()):
        base = np.linspace(1.0 + offset * 0.1, 1.4 + offset * 0.1, len(dates))
        for date, close in zip(dates, base, strict=True):
            daily_rows.append(
                {
                    "symbol": symbol,
                    "trade_date": date,
                    "adj_close": close,
                }
            )
    daily = pd.DataFrame(daily_rows)
    calendar = pd.DataFrame({"calendar_date": dates, "is_open": True})
    benchmark = pd.DataFrame(
        {
            "trade_date": dates,
            "close": np.linspace(3500, 4000, len(dates)),
        }
    )
    universe = pd.DataFrame(
        {
            "symbol": list(symbols),
            "asset_class": list(symbols.values()),
        }
    )
    variant = AllocationVariant(
        name="test",
        risk_on={EQUITY: 0.55, BOND: 0.25, GOLD: 0.10, MONEY: 0.05},
        elevated_volatility={EQUITY: 0.40, BOND: 0.35, GOLD: 0.05, MONEY: 0.10},
        risk_off={EQUITY: 0.15, BOND: 0.45, GOLD: 0.05, MONEY: 0.25},
        trend_days=20,
        volatility_days=10,
    )

    bundle = build_asset_class_signals(
        daily,
        calendar,
        benchmark,
        universe,
        _config(),
        variant,
    )

    assert bundle.execution_targets
    for execution_date, weights in bundle.execution_targets.items():
        assert execution_date > bundle.execution_signals[execution_date]
        assert 0 <= sum(weights.values()) <= 1
        assert set(weights) <= set(symbols)
    assert any(bundle.execution_targets.values())


def test_core_satellite_strategy_uses_trend_consensus_and_bounded_weights() -> None:
    dates = pd.bdate_range("2024-01-01", periods=280)
    symbols = {
        "510300": EQUITY,
        "512890": EQUITY,
        "511010": BOND,
        "518880": GOLD,
        "511880": MONEY,
    }
    daily_rows = []
    for offset, (symbol, _) in enumerate(symbols.items()):
        base = np.linspace(1.0 + offset * 0.1, 1.8 + offset * 0.1, len(dates))
        for date, close in zip(dates, base, strict=True):
            daily_rows.append(
                {
                    "symbol": symbol,
                    "trade_date": date,
                    "adj_close": close,
                }
            )
    daily = pd.DataFrame(daily_rows)
    calendar = pd.DataFrame({"calendar_date": dates, "is_open": True})
    benchmark = pd.DataFrame(
        {
            "trade_date": dates,
            "close": np.linspace(3500, 4200, len(dates)),
        }
    )
    universe = pd.DataFrame(
        {
            "symbol": list(symbols),
            "asset_class": list(symbols.values()),
        }
    )
    variant = CoreSatelliteVariant(
        name="test",
        risk_on={CORE: 0.45, SATELLITE: 0.20, BOND: 0.15, GOLD: 0.05, MONEY: 0.10},
        elevated_volatility={
            CORE: 0.30,
            SATELLITE: 0.10,
            BOND: 0.30,
            GOLD: 0.05,
            MONEY: 0.20,
        },
        risk_off={CORE: 0.05, SATELLITE: 0.0, BOND: 0.35, GOLD: 0.05, MONEY: 0.50},
    )
    config = replace(
        _config(),
        momentum_windows=(60, 120, 240),
        trend_windows=(60, 120, 240),
        trend_consensus_ratio=2 / 3,
    )

    bundle = build_core_satellite_signals(
        daily,
        calendar,
        benchmark,
        universe,
        config,
        variant,
    )

    assert any(bundle.execution_targets.values())
    for execution_date, weights in bundle.execution_targets.items():
        assert execution_date > bundle.execution_signals[execution_date]
        assert sum(weights.values()) <= 1
    ranked = bundle.rankings
    assert "trend_consensus" in ranked.columns
    assert ranked["trend_consensus"].dropna().ge(2 / 3).all()
