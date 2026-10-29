"""Effective-dated ETF lot, settlement, tick, and price-limit rules."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class TradingRule:
    symbol: str
    effective_from: pd.Timestamp
    effective_to: pd.Timestamp | None
    price_limit_pct: Decimal
    tick_size: Decimal
    buy_lot_size: int
    sell_lot_size: int
    settlement_days: int

    def applies_on(self, trade_date: object) -> bool:
        current = pd.Timestamp(trade_date).normalize()
        return current >= self.effective_from and (
            self.effective_to is None or current <= self.effective_to
        )

    def price_limits(self, previous_close: object) -> tuple[Decimal, Decimal]:
        previous = Decimal(str(previous_close))
        if previous <= 0:
            raise ValueError("Previous close must be positive")
        upper = previous * (Decimal("1") + self.price_limit_pct)
        lower = previous * (Decimal("1") - self.price_limit_pct)
        return (
            upper.quantize(self.tick_size, rounding=ROUND_HALF_UP),
            lower.quantize(self.tick_size, rounding=ROUND_HALF_UP),
        )


class TradingRuleBook:
    def __init__(self, rules: list[TradingRule]) -> None:
        self.rules = rules
        self._by_symbol: dict[str, list[TradingRule]] = {}
        for rule in rules:
            self._by_symbol.setdefault(rule.symbol, []).append(rule)
        for symbol_rules in self._by_symbol.values():
            symbol_rules.sort(key=lambda rule: rule.effective_from)

    def for_date(self, symbol: str, trade_date: object) -> TradingRule:
        normalized = str(symbol).zfill(6)
        for rule in self._by_symbol.get(normalized, []):
            if rule.applies_on(trade_date):
                return rule
        raise ValueError(f"No trading rule for {normalized} on {pd.Timestamp(trade_date).date()}")

    def symbols(self) -> set[str]:
        return set(self._by_symbol)


def load_trading_rules(
    project_root: Path,
    *,
    universe_symbols: set[str] | None = None,
) -> TradingRuleBook:
    path = project_root / "configs" / "trading_rules.csv"
    raw = pd.read_csv(path, dtype={"symbol": str, "effective_from": str, "effective_to": str})
    required = {
        "symbol",
        "effective_from",
        "price_limit_pct",
        "tick_size",
        "buy_lot_size",
        "sell_lot_size",
        "settlement_days",
    }
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(f"Trading rules are missing columns: {missing}")

    rules: list[TradingRule] = []
    for row in raw.itertuples(index=False):
        effective_from = pd.Timestamp(row.effective_from).normalize()
        effective_to = (
            pd.Timestamp(row.effective_to).normalize()
            if isinstance(row.effective_to, str) and row.effective_to.strip()
            else None
        )
        if effective_to is not None and effective_to < effective_from:
            raise ValueError(f"Trading rule end precedes start for {row.symbol}")
        rule = TradingRule(
            symbol=str(row.symbol).zfill(6),
            effective_from=effective_from,
            effective_to=effective_to,
            price_limit_pct=Decimal(str(row.price_limit_pct)),
            tick_size=Decimal(str(row.tick_size)),
            buy_lot_size=int(row.buy_lot_size),
            sell_lot_size=int(row.sell_lot_size),
            settlement_days=int(row.settlement_days),
        )
        if not Decimal("0") < rule.price_limit_pct < Decimal("0.5"):
            raise ValueError(f"Invalid price limit for {rule.symbol}")
        if rule.tick_size <= 0 or rule.buy_lot_size <= 0 or rule.sell_lot_size <= 0:
            raise ValueError(f"Invalid tick or lot size for {rule.symbol}")
        rules.append(rule)

    book = TradingRuleBook(rules)
    if universe_symbols is not None:
        missing_symbols = sorted(universe_symbols - book.symbols())
        if missing_symbols:
            raise ValueError(f"Trading rules are missing universe symbols: {missing_symbols}")

    for symbol, symbol_rules in book._by_symbol.items():
        for previous, current in zip(symbol_rules, symbol_rules[1:], strict=False):
            if previous.effective_to is None or previous.effective_to >= current.effective_from:
                raise ValueError(f"Trading rule periods overlap for {symbol}")
    return book
