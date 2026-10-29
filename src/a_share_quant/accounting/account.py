"""Cash, positions, trade ledger, and corporate actions with exact money arithmetic."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

import pandas as pd

MONEY_STEP = Decimal("0.01")
QUANTITY_STEP = Decimal("0.00000001")


def _decimal(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def _quantity(value: Decimal) -> Decimal:
    return value.quantize(QUANTITY_STEP, rounding=ROUND_HALF_UP)


@dataclass
class Position:
    symbol: str
    quantity: Decimal = Decimal("0")
    available_quantity: Decimal = Decimal("0")
    cost_basis: Decimal = Decimal("0")

    @property
    def average_cost(self) -> Decimal:
        return self.cost_basis / self.quantity if self.quantity else Decimal("0")


@dataclass(frozen=True)
class LedgerEntry:
    sequence: int
    trade_date: pd.Timestamp
    event_type: Literal["BUY", "SELL", "CASH_DISTRIBUTION", "SHARE_ADJUSTMENT"]
    symbol: str
    quantity_delta: Decimal
    price: Decimal | None
    cash_delta: Decimal
    fees: Decimal
    cash_balance: Decimal
    position_after: Decimal
    available_after: Decimal
    note: str


@dataclass(frozen=True)
class AccountSnapshot:
    trade_date: pd.Timestamp
    cash: Decimal
    market_value: Decimal
    equity: Decimal
    total_return: Decimal
    position_quantities: tuple[tuple[str, Decimal], ...]
    available_quantities: tuple[tuple[str, Decimal], ...]

    @property
    def reconciled(self) -> bool:
        return _money(self.cash + self.market_value) == _money(self.equity)


class Account:
    """Long-only research ledger. Trading costs and T+1 are added in later steps."""

    def __init__(
        self,
        initial_cash: object,
        *,
        lot_size: int = 100,
        settlement_days: int = 1,
    ) -> None:
        self.initial_cash = _money(_decimal(initial_cash))
        if self.initial_cash <= 0:
            raise ValueError("Initial cash must be positive")
        if lot_size <= 0:
            raise ValueError("Lot size must be positive")
        if settlement_days < 0:
            raise ValueError("Settlement days must not be negative")
        self.cash = self.initial_cash
        self.lot_size = lot_size
        self.settlement_days = settlement_days
        self.session_date: pd.Timestamp | None = None
        self.positions: dict[str, Position] = {}
        self.entries: list[LedgerEntry] = []

    def begin_session(self, trade_date: object) -> None:
        """Make positions bought on prior sessions available for sale."""
        current = pd.Timestamp(trade_date).normalize()
        if self.session_date is None or current > self.session_date:
            if self.settlement_days == 1:
                for position in self.positions.values():
                    position.available_quantity = position.quantity
            elif self.settlement_days > 1:
                raise NotImplementedError("Only T+0 and T+1 settlement are implemented")
            self.session_date = current

    def _position(self, symbol: str) -> Position:
        normalized = str(symbol).zfill(6)
        return self.positions.setdefault(normalized, Position(symbol=normalized))

    def _record(
        self,
        *,
        trade_date: pd.Timestamp,
        event_type: Literal["BUY", "SELL", "CASH_DISTRIBUTION", "SHARE_ADJUSTMENT"],
        symbol: str,
        quantity_delta: Decimal,
        price: Decimal | None,
        cash_delta: Decimal,
        fees: Decimal = Decimal("0"),
        note: str,
    ) -> None:
        position = self.positions.get(symbol, Position(symbol=symbol))
        self.entries.append(
            LedgerEntry(
                sequence=len(self.entries) + 1,
                trade_date=pd.Timestamp(trade_date).normalize(),
                event_type=event_type,
                symbol=symbol,
                quantity_delta=_quantity(quantity_delta),
                price=price,
                cash_delta=_money(cash_delta),
                fees=_money(fees),
                cash_balance=self.cash,
                position_after=_quantity(position.quantity),
                available_after=_quantity(position.available_quantity),
                note=note,
            )
        )

    def buy(
        self,
        trade_date: object,
        symbol: str,
        price: object,
        quantity: object,
        *,
        fee: object = Decimal("0"),
        note: str = "",
    ) -> None:
        normalized = str(symbol).zfill(6)
        unit_price = _decimal(price)
        units = _decimal(quantity)
        lot = Decimal(self.lot_size)
        if unit_price <= 0 or units <= 0:
            raise ValueError("Buy price and quantity must be positive")
        if units % lot != 0:
            raise ValueError(f"Buy quantity must be a multiple of {self.lot_size}")

        self.begin_session(trade_date)
        amount = _money(unit_price * units)
        fees = _money(_decimal(fee))
        if fees < 0:
            raise ValueError("Buy fee must not be negative")
        if amount + fees > self.cash:
            raise ValueError(f"Insufficient cash to buy {symbol}")
        position = self._position(normalized)
        self.cash -= amount + fees
        position.quantity = _quantity(position.quantity + units)
        position.cost_basis += amount + fees
        self._record(
            trade_date=pd.Timestamp(trade_date),
            event_type="BUY",
            symbol=normalized,
            quantity_delta=units,
            price=unit_price,
            cash_delta=-(amount + fees),
            fees=fees,
            note=note or "buy",
        )

    def sell(
        self,
        trade_date: object,
        symbol: str,
        price: object,
        quantity: object,
        *,
        fee: object = Decimal("0"),
        note: str = "",
    ) -> Decimal:
        normalized = str(symbol).zfill(6)
        position = self.positions.get(normalized)
        unit_price = _decimal(price)
        units = _decimal(quantity)
        self.begin_session(trade_date)
        position = self.positions.get(normalized)
        if position is None or units <= 0 or units > position.available_quantity:
            raise ValueError(f"Invalid sell quantity for {symbol}")

        average_cost = position.average_cost
        proceeds = _money(unit_price * units)
        fees = _money(_decimal(fee))
        if fees < 0:
            raise ValueError("Sell fee must not be negative")
        removed_cost = _money(average_cost * units)
        realized = _money(proceeds - fees - removed_cost)
        self.cash += proceeds - fees
        position.quantity = _quantity(position.quantity - units)
        position.available_quantity = _quantity(position.available_quantity - units)
        position.cost_basis = max(Decimal("0"), position.cost_basis - removed_cost)
        if position.quantity == 0:
            position.cost_basis = Decimal("0")
        self._record(
            trade_date=pd.Timestamp(trade_date),
            event_type="SELL",
            symbol=normalized,
            quantity_delta=-units,
            price=unit_price,
            cash_delta=proceeds - fees,
            fees=fees,
            note=note or f"sell; realized_pnl={realized}",
        )
        return realized

    def apply_cash_distribution(
        self,
        event_date: object,
        symbol: str,
        cash_per_unit: object,
        *,
        note: str = "",
    ) -> Decimal:
        normalized = str(symbol).zfill(6)
        position = self.positions.get(normalized)
        per_unit = _decimal(cash_per_unit)
        if per_unit < 0:
            raise ValueError("Cash distribution per unit must not be negative")
        amount = (
            _money(position.quantity * per_unit)
            if position is not None and position.quantity > 0
            else Decimal("0")
        )
        self.cash += amount
        self._record(
            trade_date=pd.Timestamp(event_date),
            event_type="CASH_DISTRIBUTION",
            symbol=normalized,
            quantity_delta=Decimal("0"),
            price=None,
            cash_delta=amount,
            fees=Decimal("0"),
            note=note or f"cash_per_unit={per_unit}",
        )
        return amount

    def apply_share_adjustment(
        self,
        event_date: object,
        symbol: str,
        share_multiplier: object,
        *,
        note: str = "",
    ) -> Decimal:
        normalized = str(symbol).zfill(6)
        position = self.positions.get(normalized)
        multiplier = _decimal(share_multiplier)
        if multiplier <= 0:
            raise ValueError("Share adjustment multiplier must be positive")
        if position is None or position.quantity == 0:
            return Decimal("0")
        previous = position.quantity
        position.quantity = _quantity(previous * multiplier)
        position.available_quantity = _quantity(position.available_quantity * multiplier)
        delta = position.quantity - previous
        self._record(
            trade_date=pd.Timestamp(event_date),
            event_type="SHARE_ADJUSTMENT",
            symbol=normalized,
            quantity_delta=delta,
            price=None,
            cash_delta=Decimal("0"),
            fees=Decimal("0"),
            note=note or f"share_multiplier={multiplier}",
        )
        return delta

    def mark_to_market(
        self,
        trade_date: object,
        prices: dict[str, object],
    ) -> AccountSnapshot:
        self.begin_session(trade_date)
        market_value = Decimal("0")
        quantities: list[tuple[str, Decimal]] = []
        available: list[tuple[str, Decimal]] = []
        for symbol, position in sorted(self.positions.items()):
            if position.quantity == 0:
                continue
            if symbol not in prices:
                raise ValueError(f"Missing market price for open position {symbol}")
            market_value += _money(position.quantity * _decimal(prices[symbol]))
            quantities.append((symbol, position.quantity))
            available.append((symbol, position.available_quantity))
        equity = _money(self.cash + market_value)
        return AccountSnapshot(
            trade_date=pd.Timestamp(trade_date).normalize(),
            cash=_money(self.cash),
            market_value=_money(market_value),
            equity=equity,
            total_return=(equity / self.initial_cash - Decimal("1")).quantize(
                Decimal("0.00000001")
            ),
            position_quantities=tuple(quantities),
            available_quantities=tuple(available),
        )

    def ledger_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "sequence": entry.sequence,
                    "trade_date": entry.trade_date,
                    "event_type": entry.event_type,
                    "symbol": entry.symbol,
                    "quantity_delta": float(entry.quantity_delta),
                    "price": float(entry.price) if entry.price is not None else None,
                    "cash_delta": float(entry.cash_delta),
                    "fees": float(entry.fees),
                    "cash_balance": float(entry.cash_balance),
                    "position_after": float(entry.position_after),
                    "available_after": float(entry.available_after),
                    "note": entry.note,
                }
                for entry in self.entries
            ]
        )

    def audit(self) -> list[str]:
        errors: list[str] = []
        if self.cash < 0:
            errors.append("Account cash is negative")
        for symbol, position in self.positions.items():
            if position.quantity < 0:
                errors.append(f"Position {symbol} has negative quantity")
            if position.available_quantity < 0 or position.available_quantity > position.quantity:
                errors.append(f"Position {symbol} has invalid available quantity")
            if position.quantity == 0 and position.cost_basis != 0:
                errors.append(f"Closed position {symbol} retains cost basis")
        ledger_cash = self.initial_cash + sum(
            (entry.cash_delta for entry in self.entries), Decimal("0")
        )
        if _money(ledger_cash) != _money(self.cash):
            errors.append("Cash balance does not reconcile to the event ledger")
        return errors
