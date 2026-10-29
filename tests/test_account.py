from decimal import Decimal

import pandas as pd
import pytest

from a_share_quant.accounting.account import Account
from a_share_quant.accounting.actions import (
    derive_share_adjustments,
    normalize_cash_distributions,
)
from a_share_quant.accounting.baseline import (
    run_equal_weight_buy_hold,
    run_single_asset_buy_hold,
)


def _daily(
    *,
    symbol: str = "510300",
    dates: list[str] | None = None,
    opens: list[float] | None = None,
    closes: list[float] | None = None,
) -> pd.DataFrame:
    dates = dates or ["2026-01-05", "2026-01-06", "2026-01-07"]
    opens = opens or [10.0, 10.2, 10.8]
    closes = closes or [10.0, 10.5, 11.0]
    return pd.DataFrame(
        {
            "symbol": symbol,
            "trade_date": pd.to_datetime(dates),
            "open": opens,
            "high": [
                max(open_price, close) * 1.01
                for open_price, close in zip(opens, closes, strict=True)
            ],
            "low": [
                min(open_price, close) * 0.99
                for open_price, close in zip(opens, closes, strict=True)
            ],
            "close": closes,
            "adj_close": closes,
            "adjustment_factor": [1.0] * len(dates),
            "source": ["test"] * len(dates),
            "retrieved_at": ["2026-01-08T00:00:00+08:00"] * len(dates),
        }
    )


def _actions(
    *,
    symbol: str = "510300",
    event_date: str = "2026-01-06",
    action_type: str = "cash_distribution",
    cash_per_unit: float = 0.5,
    share_multiplier: float = 1.0,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": [symbol],
            "event_date": [pd.Timestamp(event_date)],
            "action_type": [action_type],
            "cash_per_unit": [cash_per_unit],
            "share_multiplier": [share_multiplier],
            "source": ["test"],
            "retrieved_at": ["2026-01-08T00:00:00+08:00"],
            "derivation": ["test"],
        }
    )


def test_account_buy_mark_and_ledger_reconcile() -> None:
    account = Account(1000)
    account.buy("2026-01-05", "510300", 2, 100)
    snapshot = account.mark_to_market("2026-01-05", {"510300": 2.5})

    assert account.cash == Decimal("800.00")
    assert snapshot.market_value == Decimal("250.00")
    assert snapshot.equity == Decimal("1050.00")
    assert snapshot.reconciled
    assert account.audit() == []


def test_account_enforces_lots_and_cash() -> None:
    account = Account(1000)
    with pytest.raises(ValueError, match="multiple of 100"):
        account.buy("2026-01-05", "510300", 2, 50)
    with pytest.raises(ValueError, match="Insufficient cash"):
        account.buy("2026-01-05", "510300", 20, 100)


def test_account_sell_realizes_profit() -> None:
    account = Account(1000)
    account.buy("2026-01-05", "510300", 2, 100)
    account.begin_session("2026-01-06")
    realized = account.sell("2026-01-06", "510300", 3, 100)

    assert realized == Decimal("100.00")
    assert account.cash == Decimal("1100.00")
    assert account.positions["510300"].quantity == 0
    assert account.audit() == []


def test_cash_distribution_and_split_are_recorded() -> None:
    account = Account(1000)
    account.buy("2026-01-05", "510300", 2, 100)
    account.begin_session("2026-01-06")

    cash = account.apply_cash_distribution("2026-01-06", "510300", 0.1)
    share_delta = account.apply_share_adjustment("2026-01-07", "510300", 0.5)

    assert cash == Decimal("10.00")
    assert share_delta == Decimal("-50")
    assert account.positions["510300"].quantity == Decimal("50")
    assert [entry.event_type for entry in account.entries] == [
        "BUY",
        "CASH_DISTRIBUTION",
        "SHARE_ADJUSTMENT",
    ]


def test_t_plus_one_blocks_same_day_sell() -> None:
    account = Account(1000)
    account.buy("2026-01-05", "510300", 2, 100)

    with pytest.raises(ValueError, match="Invalid sell quantity"):
        account.sell("2026-01-05", "510300", 2.1, 100)

    account.begin_session("2026-01-06")
    assert account.positions["510300"].available_quantity == 100


def test_single_buy_hold_includes_cash_distribution() -> None:
    result = run_single_asset_buy_hold(
        _daily(),
        _actions(),
        symbol="510300",
        initial_cash=12000,
        lot_size=100,
    )

    assert result.summary["final_equity"] == 13800.0
    assert result.summary["company_action_entries"] == 1
    assert result.snapshots["reconciled"].all()
    assert result.ledger["event_type"].tolist() == ["BUY", "CASH_DISTRIBUTION"]


def test_equal_weight_buy_hold_handles_multiple_positions() -> None:
    daily = pd.concat(
        [
            _daily(symbol="510300"),
            _daily(symbol="510500", opens=[5.0, 5.1, 5.2], closes=[5.0, 5.2, 5.4]),
        ],
        ignore_index=True,
    )
    actions = pd.concat([_actions(), _actions(symbol="510500", cash_per_unit=0.2)])

    result = run_equal_weight_buy_hold(
        daily,
        actions,
        initial_cash=20000,
        lot_size=100,
    )

    assert result.snapshots["reconciled"].all()
    assert result.summary["company_action_entries"] == 2
    assert len(result.snapshots) == 3


def test_cumulative_distribution_is_converted_to_increments() -> None:
    raw = pd.DataFrame(
        {
            "日期": ["2024-01-02", "2025-01-02"],
            "累计分红": [0.10, 0.35],
        }
    )
    result = normalize_cash_distributions(
        raw,
        symbol="510300",
        source="test",
        retrieved_at="2026-01-01T00:00:00+08:00",
    )

    assert result["cash_per_unit"].tolist() == pytest.approx([0.10, 0.25])


def test_share_adjustment_is_derived_from_return_discontinuity() -> None:
    daily = _daily(opens=[10.0, 3.0, 3.1], closes=[10.0, 3.0, 3.1])
    daily["adj_close"] = [10.0, 10.0, 10.333]
    daily["adjustment_factor"] = [1.0, 10 / 3, 10.333 / 3.1]

    actions = derive_share_adjustments(daily, pd.DataFrame())

    assert actions.empty is False
    assert actions.iloc[0]["event_date"] == pd.Timestamp("2026-01-06")
    assert actions.iloc[0]["share_multiplier"] == pytest.approx(10 / 3)
