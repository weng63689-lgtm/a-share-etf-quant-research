from decimal import Decimal

import pandas as pd

from a_share_quant.accounting.account import Account
from a_share_quant.execution.order import OrderRequest, TradingConstraintEngine
from a_share_quant.execution.rules import TradingRule, TradingRuleBook


def _rule_book() -> TradingRuleBook:
    return TradingRuleBook(
        [
            TradingRule(
                symbol="510300",
                effective_from=pd.Timestamp("2012-01-01"),
                effective_to=None,
                price_limit_pct=Decimal("0.10"),
                tick_size=Decimal("0.001"),
                buy_lot_size=100,
                sell_lot_size=100,
                settlement_days=1,
            )
        ]
    )


def _bar(*, open_price: float = 10.0, volume: float = 1000, turnover: float = 10000) -> pd.Series:
    return pd.Series(
        {
            "open": open_price,
            "high": max(open_price, 10.0) * 1.01,
            "low": min(open_price, 10.0) * 0.99,
            "close": 10.0,
            "volume": volume,
            "turnover": turnover,
        }
    )


def _order(side: str, *, execution_date: str) -> OrderRequest:
    return OrderRequest(
        symbol="510300",
        side=side,
        quantity=Decimal("100"),
        signal_date=pd.Timestamp("2026-01-04"),
        execution_date=pd.Timestamp(execution_date),
    )


def test_t_plus_one_rejects_same_day_sell() -> None:
    account = Account(10000)
    engine = TradingConstraintEngine(_rule_book())
    buy = engine.execute(
        account,
        _order("BUY", execution_date="2026-01-05"),
        bar=_bar(),
        previous_close=10.0,
    )
    sell = engine.execute(
        account,
        _order("SELL", execution_date="2026-01-05"),
        bar=_bar(),
        previous_close=10.0,
    )

    assert buy.status == "FILLED"
    assert sell.status == "REJECTED"
    assert sell.reason == "T_PLUS_ONE_UNAVAILABLE"


def test_t_plus_one_position_can_be_sold_next_session() -> None:
    account = Account(10000)
    engine = TradingConstraintEngine(_rule_book())
    engine.execute(
        account,
        _order("BUY", execution_date="2026-01-05"),
        bar=_bar(),
        previous_close=10.0,
    )
    sell = engine.execute(
        account,
        _order("SELL", execution_date="2026-01-06"),
        bar=_bar(open_price=10.1),
        previous_close=10.0,
    )

    assert sell.status == "FILLED"
    assert account.positions["510300"].quantity == 0


def test_missing_bar_and_zero_volume_reject_orders() -> None:
    engine = TradingConstraintEngine(_rule_book())
    account = Account(10000)

    missing = engine.execute(
        account,
        _order("BUY", execution_date="2026-01-05"),
        bar=None,
        previous_close=10.0,
    )
    zero_volume = engine.execute(
        account,
        _order("BUY", execution_date="2026-01-05"),
        bar=_bar(volume=0),
        previous_close=10.0,
    )

    assert missing.reason == "SUSPENDED_OR_MISSING_BAR"
    assert zero_volume.reason == "NO_TRADING_VOLUME"


def test_limit_up_buy_and_limit_down_sell_are_blocked() -> None:
    engine = TradingConstraintEngine(_rule_book())
    buy_account = Account(10000)
    blocked_buy = engine.execute(
        buy_account,
        _order("BUY", execution_date="2026-01-05"),
        bar=_bar(open_price=11.0),
        previous_close=10.0,
    )

    sell_account = Account(10000)
    sell_account.buy("2026-01-05", "510300", 10.0, 100)
    blocked_sell = engine.execute(
        sell_account,
        _order("SELL", execution_date="2026-01-06"),
        bar=_bar(open_price=9.0),
        previous_close=10.0,
    )

    assert blocked_buy.reason == "LIMIT_UP_BUY_BLOCKED"
    assert blocked_sell.reason == "LIMIT_DOWN_SELL_BLOCKED"


def test_share_adjustment_reference_avoids_false_limit_rejection() -> None:
    engine = TradingConstraintEngine(_rule_book())
    account = Account(10000)
    result = engine.execute(
        account,
        _order("BUY", execution_date="2026-01-05"),
        bar=_bar(open_price=8.1),
        previous_close=2.243,
        limit_reference_close=8.01,
    )

    assert result.status == "FILLED"


def test_invalid_lot_and_non_next_session_order_are_rejected() -> None:
    engine = TradingConstraintEngine(_rule_book())
    account = Account(10000)
    invalid_lot = engine.execute(
        account,
        OrderRequest(
            symbol="510300",
            side="BUY",
            quantity=Decimal("50"),
            signal_date=pd.Timestamp("2026-01-04"),
            execution_date=pd.Timestamp("2026-01-05"),
        ),
        bar=_bar(),
        previous_close=10.0,
    )
    same_time = engine.execute(
        account,
        OrderRequest(
            symbol="510300",
            side="BUY",
            quantity=Decimal("100"),
            signal_date=pd.Timestamp("2026-01-05"),
            execution_date=pd.Timestamp("2026-01-05"),
        ),
        bar=_bar(),
        previous_close=10.0,
    )

    assert invalid_lot.reason == "INVALID_BUY_LOT"
    assert same_time.reason == "SIGNAL_NOT_BEFORE_EXECUTION"


def test_chinext_price_limit_changes_by_effective_date() -> None:
    book = TradingRuleBook(
        [
            TradingRule(
                symbol="159915",
                effective_from=pd.Timestamp("2012-01-01"),
                effective_to=pd.Timestamp("2020-08-23"),
                price_limit_pct=Decimal("0.10"),
                tick_size=Decimal("0.001"),
                buy_lot_size=100,
                sell_lot_size=100,
                settlement_days=1,
            ),
            TradingRule(
                symbol="159915",
                effective_from=pd.Timestamp("2020-08-24"),
                effective_to=None,
                price_limit_pct=Decimal("0.20"),
                tick_size=Decimal("0.001"),
                buy_lot_size=100,
                sell_lot_size=100,
                settlement_days=1,
            ),
        ]
    )

    assert book.for_date("159915", "2020-08-23").price_limit_pct == Decimal("0.10")
    assert book.for_date("159915", "2020-08-24").price_limit_pct == Decimal("0.20")
