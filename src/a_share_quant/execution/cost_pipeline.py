"""Run reproducible transaction-cost and stress scenarios on recent ETF liquidity."""

from decimal import ROUND_DOWN, Decimal
from pathlib import Path

import pandas as pd

from a_share_quant.data.storage import DataStore
from a_share_quant.execution.costs import load_cost_model
from a_share_quant.execution.rules import load_trading_rules


class CostAuditPipeline:
    def __init__(self, project_root: Path) -> None:
        self.store = DataStore(project_root)
        self.cost_model = load_cost_model(project_root)

    def audit(self) -> dict[str, object]:
        daily = self.store.load_processed("etf_daily")
        universe = self.store.load_universe()
        rule_book = load_trading_rules(
            self.store.project_root,
            universe_symbols=set(universe["symbol"].astype(str).str.zfill(6)),
        )
        rows: list[dict[str, object]] = []
        errors: list[str] = []
        target_notionals = (Decimal("50000"), Decimal("500000"))

        for symbol, frame in daily.groupby("symbol", sort=True):
            symbol = str(symbol).zfill(6)
            tradable = frame[(frame["turnover"] > 0) & (frame["open"] > 0)]
            if tradable.empty:
                errors.append(f"No tradable liquidity observation for {symbol}")
                continue
            latest = tradable.sort_values("trade_date").iloc[-1]
            rule = rule_book.for_date(symbol, latest["trade_date"])
            price = Decimal(str(latest["open"]))
            turnover = Decimal(str(latest["turnover"]))
            for notional in target_notionals:
                quantity = (
                    notional / price / Decimal(rule.buy_lot_size)
                ).to_integral_value(rounding=ROUND_DOWN) * Decimal(rule.buy_lot_size)
                if quantity <= 0:
                    errors.append(f"Target notional is below one lot for {symbol}")
                    continue
                for multiplier in self.cost_model.config.stress_multipliers:
                    for side in ("BUY", "SELL"):
                        quote = self.cost_model.quote(
                            side=side,
                            market_price=price,
                            quantity=quantity,
                            turnover=turnover,
                            rule=rule,
                            stress_multiplier=multiplier,
                        )
                        cost_bps = quote.total_cost / quote.gross_notional * Decimal("10000")
                        rows.append(
                            {
                                "symbol": symbol,
                                "trade_date": latest["trade_date"],
                                "side": side,
                                "target_notional": float(notional),
                                "quantity": float(quantity),
                                "market_price": float(quote.market_price),
                                "fill_price": float(quote.fill_price),
                                "turnover": float(turnover),
                                "participation": float(quote.participation),
                                "stress_multiplier": float(multiplier),
                                "commission": float(quote.commission),
                                "stamp_duty": float(quote.stamp_duty),
                                "transfer_fee": float(quote.transfer_fee),
                                "slippage_cost": float(quote.slippage_cost),
                                "impact_cost": float(quote.impact_cost),
                                "total_cost": float(quote.total_cost),
                                "cost_bps": float(cost_bps),
                                "capacity_quantity": float(quote.capacity_quantity),
                            }
                        )

        scenarios = pd.DataFrame(rows)
        if scenarios.empty:
            errors.append("Cost audit produced no scenarios")
        grouped = (
            scenarios.groupby(["stress_multiplier", "side"])
            .agg(
                scenarios=("symbol", "size"),
                average_cost_bps=("cost_bps", "mean"),
                average_cost_cny=("total_cost", "mean"),
                maximum_cost_bps=("cost_bps", "max"),
            )
            .reset_index()
            if not scenarios.empty
            else pd.DataFrame()
        )
        report = {
            "status": "ok" if not errors else "failed",
            "errors": errors,
            "checked_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "assumptions": {
                "commission_rate": str(self.cost_model.config.commission_rate),
                "minimum_commission_cny": str(self.cost_model.config.minimum_commission),
                "stamp_duty_rate": str(self.cost_model.config.stamp_duty_rate),
                "transfer_fee_rate": str(self.cost_model.config.transfer_fee_rate),
                "base_slippage_bps": str(self.cost_model.config.base_slippage_bps),
                "impact_coefficient_bps": str(self.cost_model.config.impact_coefficient_bps),
                "max_participation": str(self.cost_model.config.max_participation),
                "stress_multipliers": [
                    str(value) for value in self.cost_model.config.stress_multipliers
                ],
            },
            "scenarios": len(scenarios),
            "summary": grouped.to_dict(orient="records"),
        }
        self.store.save_processed(scenarios, "cost_model_scenarios")
        self.store.update_cost_database(scenarios)
        self.store.save_cost_model_report(report)
        if errors:
            raise ValueError("Cost model audit failed: " + "; ".join(errors))
        return report

    def summary(self) -> dict[str, object]:
        path = self.store.processed_dir / "cost_model_audit.json"
        if not path.is_file():
            raise FileNotFoundError("Run 'uv run quant-cost audit' first")
        import json

        return json.loads(path.read_text(encoding="utf-8"))
