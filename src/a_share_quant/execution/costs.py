"""Configurable ETF commission, tax, slippage, impact, and capacity model."""

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from pathlib import Path

import yaml

from a_share_quant.execution.rules import TradingRule


@dataclass(frozen=True)
class CostConfig:
    commission_rate: Decimal
    minimum_commission: Decimal
    stamp_duty_rate: Decimal
    transfer_fee_rate: Decimal
    base_slippage_bps: Decimal
    impact_coefficient_bps: Decimal
    impact_reference_participation: Decimal
    impact_exponent: Decimal
    max_participation: Decimal
    stress_multipliers: tuple[Decimal, ...]


@dataclass(frozen=True)
class CostQuote:
    side: str
    market_price: Decimal
    fill_price: Decimal
    quantity: Decimal
    gross_notional: Decimal
    commission: Decimal
    stamp_duty: Decimal
    transfer_fee: Decimal
    slippage_cost: Decimal
    impact_cost: Decimal
    total_cost: Decimal
    participation: Decimal
    stress_multiplier: Decimal
    capacity_quantity: Decimal


class CapacityExceeded(ValueError):
    """Raised when a requested order exceeds the configured daily participation cap."""


class CostModel:
    def __init__(self, config: CostConfig) -> None:
        self.config = config
        self._validate()

    def _validate(self) -> None:
        config = self.config
        for name in (
            "commission_rate",
            "stamp_duty_rate",
            "transfer_fee_rate",
            "base_slippage_bps",
            "impact_coefficient_bps",
            "impact_reference_participation",
            "impact_exponent",
            "max_participation",
        ):
            if getattr(config, name) < 0:
                raise ValueError(f"Cost setting {name} must not be negative")
        if config.minimum_commission < 0:
            raise ValueError("minimum_commission must not be negative")
        if not 0 < config.max_participation <= 1:
            raise ValueError("max_participation must be in (0, 1]")
        if config.impact_reference_participation <= 0:
            raise ValueError("impact_reference_participation must be positive")
        if not config.stress_multipliers or min(config.stress_multipliers) <= 0:
            raise ValueError("stress_multipliers must contain positive values")

    def capacity_quantity(
        self,
        *,
        turnover: object,
        market_price: object,
        lot_size: int,
    ) -> Decimal:
        turnover_decimal = Decimal(str(turnover))
        price = Decimal(str(market_price))
        if turnover_decimal <= 0 or price <= 0 or lot_size <= 0:
            return Decimal("0")
        raw_quantity = (
            turnover_decimal * self.config.max_participation / price / Decimal(lot_size)
        ).to_integral_value(rounding=ROUND_DOWN)
        return raw_quantity * Decimal(lot_size)

    def quote(
        self,
        *,
        side: str,
        market_price: object,
        quantity: object,
        turnover: object,
        rule: TradingRule,
        stress_multiplier: object = Decimal("1"),
    ) -> CostQuote:
        if side not in {"BUY", "SELL"}:
            raise ValueError("Cost side must be BUY or SELL")
        price = Decimal(str(market_price))
        units = Decimal(str(quantity))
        turnover_decimal = Decimal(str(turnover))
        stress = Decimal(str(stress_multiplier))
        if price <= 0 or units <= 0 or turnover_decimal <= 0 or stress <= 0:
            raise ValueError("Price, quantity, turnover, and stress must be positive")

        capacity = self.capacity_quantity(
            turnover=turnover_decimal,
            market_price=price,
            lot_size=rule.buy_lot_size,
        )
        if units > capacity:
            raise CapacityExceeded(
                f"Order quantity {units} exceeds daily capacity {capacity}"
            )

        gross_notional = price * units
        participation = gross_notional / turnover_decimal
        impact_ratio = participation / self.config.impact_reference_participation
        impact_bps = self.config.impact_coefficient_bps * (
            impact_ratio**self.config.impact_exponent
        )
        base_bps = self.config.base_slippage_bps
        adjustment_rate = (base_bps + impact_bps) * stress / Decimal("10000")
        raw_fill = (
            price * (Decimal("1") + adjustment_rate)
            if side == "BUY"
            else price * (Decimal("1") - adjustment_rate)
        )
        rounding = ROUND_UP if side == "BUY" else ROUND_DOWN
        fill_price = raw_fill.quantize(rule.tick_size, rounding=rounding)
        if fill_price <= 0:
            raise ValueError("Cost model produced a non-positive fill price")

        fill_notional = fill_price * units
        variable_commission = fill_notional * self.config.commission_rate * stress
        commission = max(variable_commission, self.config.minimum_commission)
        stamp_duty = fill_notional * self.config.stamp_duty_rate * stress
        transfer_fee = fill_notional * self.config.transfer_fee_rate * stress
        slippage_cost = abs(fill_price - price) * units
        base_component = price * units * base_bps * stress / Decimal("10000")
        impact_cost = max(Decimal("0"), slippage_cost - base_component)
        total_cost = commission + stamp_duty + transfer_fee + slippage_cost

        def cents(value: Decimal) -> Decimal:
            return value.quantize(Decimal("0.01"))

        return CostQuote(
            side=side,
            market_price=price,
            fill_price=fill_price,
            quantity=units,
            gross_notional=cents(gross_notional),
            commission=cents(commission),
            stamp_duty=cents(stamp_duty),
            transfer_fee=cents(transfer_fee),
            slippage_cost=cents(slippage_cost),
            impact_cost=cents(impact_cost),
            total_cost=cents(total_cost),
            participation=participation.quantize(Decimal("0.00000001")),
            stress_multiplier=stress,
            capacity_quantity=capacity,
        )


def load_cost_model(project_root: Path) -> CostModel:
    path = project_root / "configs" / "default.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    costs = payload.get("costs", {}) if isinstance(payload, dict) else {}
    stress_values = costs.get("stress_multipliers", [1, 2, 3])
    config = CostConfig(
        commission_rate=Decimal(str(costs.get("commission_rate", "0.0003"))),
        minimum_commission=Decimal(str(costs.get("minimum_commission_cny", "5"))),
        stamp_duty_rate=Decimal(str(costs.get("stamp_duty_rate", "0"))),
        transfer_fee_rate=Decimal(str(costs.get("transfer_fee_rate", "0"))),
        base_slippage_bps=Decimal(str(costs.get("base_slippage_bps", "2"))),
        impact_coefficient_bps=Decimal(str(costs.get("impact_coefficient_bps", "5"))),
        impact_reference_participation=Decimal(
            str(costs.get("impact_reference_participation", "0.01"))
        ),
        impact_exponent=Decimal(str(costs.get("impact_exponent", "0.5"))),
        max_participation=Decimal(str(costs.get("max_participation", "0.10"))),
        stress_multipliers=tuple(Decimal(str(value)) for value in stress_values),
    )
    return CostModel(config)
