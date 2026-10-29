from decimal import Decimal

import pandas as pd
import pytest

from a_share_quant.accounting.account import Account
from a_share_quant.execution.costs import CapacityExceeded, CostConfig, CostModel
from a_share_quant.execution.order import OrderRequest, TradingConstraintEngine
from a_share_quant.execution.rules import TradingRule, TradingRuleBook


def _rule() -> TradingRule:
    return TradingRule(
        symbol="510300",
        effective_from=pd.Timestamp("2012-01-01"),
        effective_to=None,
        price_limit_pct=Decimal("0.10"),
        tick_size=Decimal("0.001"),
        buy_lot_size=100,
        sell_lot_size=100,
        settlement_days=1,
    )


def _cost_model() -> CostModel:
    return CostModel(
        CostConfig(
            commission_rate=Decimal("0.0003"),
            minimum_commission=Decimal("5"),
            stamp_duty_rate=Decimal("0"),
            transfer_fee_rate=Decimal("0"),
            base_slippage_bps=Decimal("2"),
            impact_coefficient_bps=Decimal("5"),
            impact_reference_participation=Decimal("0.01"),
            impact_exponent=Decimal("0.5"),
            max_participation=Decimal("0.10"),
            stress_multipliers=(Decimal("1"), Decimal("2"), Decimal("3")),
        )
    )


def test_minimum_commission_and_etf_zero_stamp_duty() -> None:
    quote = _cost_model().quote(
        side="BUY",
        market_price=1,
        quantity=1000,
        turnover=1_000_000,
        rule=_rule(),
    )

    assert quote.commission == Decimal("5.00")
    assert quote.stamp_duty == Decimal("0.00")
    assert quote.total_cost > quote.commission


def test_buy_fill_is_above_and_sell_fill_below_market() -> None:
    model = _cost_model()
    buy = model.quote(
        side="BUY",
        market_price=10,
        quantity=100,
        turnover=1_000_000,
        rule=_rule(),
    )
    sell = model.quote(
        side="SELL",
        market_price=10,
        quantity=100,
        turnover=1_000_000,
        rule=_rule(),
    )

    assert buy.fill_price > Decimal("10")
    assert sell.fill_price < Decimal("10")
    assert buy.fill_price > sell.fill_price


def test_cost_stress_increases_non_linear_transaction_costs() -> None:
    model = _cost_model()
    quotes = [
        model.quote(
            side="BUY",
            market_price=4,
            quantity=10_000,
            turnover=100_000_000,
            rule=_rule(),
            stress_multiplier=stress,
        )
        for stress in (1, 2, 3)
    ]

    assert quotes[0].total_cost < quotes[1].total_cost < quotes[2].total_cost
    assert quotes[1].stress_multiplier == Decimal("2")


def test_order_above_participation_cap_is_rejected() -> None:
    model = _cost_model()
    with pytest.raises(CapacityExceeded):
        model.quote(
            side="BUY",
            market_price=10,
            quantity=200,
            turnover=10_000,
            rule=_rule(),
        )


def test_account_cash_includes_buy_fee() -> None:
    account = Account(1000)
    account.buy("2026-01-05", "510300", 2, 100, fee=5)

    assert account.cash == Decimal("795.00")
    assert account.positions["510300"].cost_basis == Decimal("205.00")
    assert account.entries[0].fees == Decimal("5.00")


def test_constraint_engine_uses_cost_model() -> None:
    rules = TradingRuleBook([_rule()])
    engine = TradingConstraintEngine(rules, cost_model=_cost_model())
    account = Account(10000)
    result = engine.execute(
        account,
        OrderRequest(
            symbol="510300",
            side="BUY",
            quantity=Decimal("100"),
            signal_date=pd.Timestamp("2026-01-04"),
            execution_date=pd.Timestamp("2026-01-05"),
        ),
        bar=pd.Series(
            {
                "open": 10.0,
                "high": 10.1,
                "low": 9.9,
                "close": 10.0,
                "volume": 1000,
                "turnover": 1_000_000,
            }
        ),
        previous_close=10.0,
    )

    assert result.status == "FILLED"
    assert result.commission == Decimal("5.00")
    assert result.fill_price is not None and result.fill_price > Decimal("10")
    assert result.total_cost > result.commission
