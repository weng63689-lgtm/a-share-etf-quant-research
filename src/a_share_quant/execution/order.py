"""Conservative daily order execution against one ETF price bar."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import pandas as pd

from a_share_quant.accounting.account import Account
from a_share_quant.execution.costs import CapacityExceeded, CostModel
from a_share_quant.execution.rules import TradingRuleBook


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    signal_date: pd.Timestamp
    execution_date: pd.Timestamp
    reason: str = ""


@dataclass(frozen=True)
class OrderResult:
    symbol: str
    side: str
    requested_quantity: Decimal
    status: Literal["FILLED", "REJECTED"]
    reason: str
    fill_price: Decimal | None = None
    fill_quantity: Decimal = Decimal("0")
    market_price: Decimal | None = None
    gross_notional: Decimal = Decimal("0")
    commission: Decimal = Decimal("0")
    slippage_cost: Decimal = Decimal("0")
    impact_cost: Decimal = Decimal("0")
    total_cost: Decimal = Decimal("0")
    stress_multiplier: Decimal = Decimal("1")


class TradingConstraintEngine:
    """Daily, long-only execution that fails closed around market constraints."""

    def __init__(
        self,
        rule_book: TradingRuleBook,
        *,
        cost_model: CostModel | None = None,
        stress_multiplier: object = Decimal("1"),
    ) -> None:
        self.rule_book = rule_book
        self.cost_model = cost_model
        self.stress_multiplier = Decimal(str(stress_multiplier))
        if self.stress_multiplier <= 0:
            raise ValueError("Stress multiplier must be positive")

    def execute(
        self,
        account: Account,
        order: OrderRequest,
        *,
        bar: pd.Series | None,
        previous_close: object | None,
        limit_reference_close: object | None = None,
    ) -> OrderResult:
        symbol = str(order.symbol).zfill(6)
        quantity = Decimal(str(order.quantity))
        signal_date = pd.Timestamp(order.signal_date).normalize()
        execution_date = pd.Timestamp(order.execution_date).normalize()
        rule = self.rule_book.for_date(symbol, execution_date)

        def reject(reason: str) -> OrderResult:
            return OrderResult(
                symbol=symbol,
                side=order.side,
                requested_quantity=quantity,
                status="REJECTED",
                reason=reason,
            )

        if order.side not in {"BUY", "SELL"}:
            return reject("INVALID_SIDE")
        if account.lot_size != rule.buy_lot_size:
            return reject("ACCOUNT_LOT_RULE_MISMATCH")
        if account.settlement_days != rule.settlement_days:
            return reject("ACCOUNT_SETTLEMENT_RULE_MISMATCH")
        if quantity <= 0:
            return reject("INVALID_QUANTITY")
        if execution_date <= signal_date:
            return reject("SIGNAL_NOT_BEFORE_EXECUTION")
        account.begin_session(execution_date)
        if bar is None:
            return reject("SUSPENDED_OR_MISSING_BAR")

        if "volume" in bar and float(bar["volume"]) <= 0:
            return reject("NO_TRADING_VOLUME")
        if "turnover" in bar and float(bar["turnover"]) <= 0:
            return reject("NO_TURNOVER")

        position = account.positions.get(symbol)
        if order.side == "SELL":
            if position is None or position.quantity <= 0:
                return reject("NO_POSITION")
            if quantity > position.available_quantity:
                return reject("T_PLUS_ONE_UNAVAILABLE")
            if (
                quantity % Decimal(rule.sell_lot_size) != 0
                and quantity != position.available_quantity
            ):
                return reject("INVALID_SELL_LOT")
        elif quantity % Decimal(rule.buy_lot_size) != 0:
            return reject("INVALID_BUY_LOT")

        if previous_close is None or pd.isna(previous_close):
            return reject("UNKNOWN_PREVIOUS_CLOSE")
        market_price = Decimal(str(bar["open"])).quantize(rule.tick_size)
        if market_price <= 0:
            return reject("INVALID_OPEN_PRICE")
        if self.cost_model is None:
            fill_price = market_price
            fee = Decimal("0")
            gross_notional = market_price * quantity
            commission = Decimal("0")
            slippage_cost = Decimal("0")
            impact_cost = Decimal("0")
            total_cost = Decimal("0")
            stress_multiplier = Decimal("1")
        else:
            try:
                quote = self.cost_model.quote(
                    side=order.side,
                    market_price=market_price,
                    quantity=quantity,
                    turnover=bar["turnover"],
                    rule=rule,
                    stress_multiplier=self.stress_multiplier,
                )
            except CapacityExceeded:
                return reject("LIQUIDITY_CAPACITY_EXCEEDED")
            fill_price = quote.fill_price
            fee = quote.commission + quote.stamp_duty + quote.transfer_fee
            gross_notional = quote.gross_notional
            commission = quote.commission
            slippage_cost = quote.slippage_cost
            impact_cost = quote.impact_cost
            total_cost = quote.total_cost
            stress_multiplier = quote.stress_multiplier
        limit_reference = (
            previous_close if limit_reference_close is None else limit_reference_close
        )
        limit_up, limit_down = rule.price_limits(limit_reference)
        if order.side == "BUY" and fill_price >= limit_up:
            return reject("LIMIT_UP_BUY_BLOCKED")
        if order.side == "SELL" and fill_price <= limit_down:
            return reject("LIMIT_DOWN_SELL_BLOCKED")

        try:
            if order.side == "BUY":
                account.buy(
                    execution_date,
                    symbol,
                    fill_price,
                    quantity,
                    fee=fee,
                    note=order.reason or "constraint-engine buy",
                )
            else:
                account.sell(
                    execution_date,
                    symbol,
                    fill_price,
                    quantity,
                    fee=fee,
                    note=order.reason or "constraint-engine sell",
                )
        except ValueError as error:
            return reject(str(error))
        return OrderResult(
            symbol=symbol,
            side=order.side,
            requested_quantity=quantity,
            status="FILLED",
            reason="FILLED_AT_NEXT_SESSION_OPEN",
            fill_price=fill_price,
            fill_quantity=quantity,
            market_price=market_price,
            gross_notional=gross_notional,
            commission=commission,
            slippage_cost=slippage_cost,
            impact_cost=impact_cost,
            total_cost=total_cost,
            stress_multiplier=stress_multiplier,
        )
