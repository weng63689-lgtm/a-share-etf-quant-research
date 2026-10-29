"""Audit effective ETF trading rules against the complete daily history."""

from pathlib import Path

import pandas as pd

from a_share_quant.data.storage import DataStore
from a_share_quant.execution.rules import load_trading_rules


class TradingConstraintPipeline:
    def __init__(self, project_root: Path) -> None:
        self.store = DataStore(project_root)

    def audit(self) -> dict[str, object]:
        daily = self.store.load_processed("etf_daily")
        actions = self.store.load_processed("etf_corporate_actions")
        universe = self.store.load_universe()
        missing = self.store.load_processed("etf_daily_missing_dates")
        rule_book = load_trading_rules(
            self.store.project_root,
            universe_symbols=set(universe["symbol"].astype(str).str.zfill(6)),
        )

        events: list[dict[str, object]] = []
        actions_by_key: dict[tuple[str, pd.Timestamp], list[object]] = {}
        for action in actions.itertuples(index=False):
            key = (str(action.symbol).zfill(6), pd.Timestamp(action.event_date).normalize())
            actions_by_key.setdefault(key, []).append(action)
        errors: list[str] = []
        for symbol, frame in daily.groupby("symbol", sort=True):
            symbol = str(symbol).zfill(6)
            frame = frame.sort_values("trade_date").reset_index(drop=True)
            previous_close = frame["close"].shift(1)
            for index, row in frame.iterrows():
                rule = rule_book.for_date(symbol, row["trade_date"])
                if row["volume"] <= 0 or row["turnover"] <= 0:
                    events.append(
                        {
                            "symbol": symbol,
                            "trade_date": row["trade_date"],
                            "event_type": "NO_TRADING_VOLUME",
                            "open": row["open"],
                            "previous_close": previous_close.loc[index],
                            "limit_price": None,
                            "detail": "zero volume or turnover",
                        }
                    )
                if pd.isna(previous_close.loc[index]):
                    continue
                limit_reference = previous_close.loc[index]
                for action in actions_by_key.get(
                    (symbol, pd.Timestamp(row["trade_date"])),
                    [],
                ):
                    if action.action_type == "share_adjustment":
                        limit_reference = limit_reference / action.share_multiplier
                    elif action.action_type == "cash_distribution":
                        limit_reference = limit_reference - action.cash_per_unit
                limit_up, limit_down = rule.price_limits(limit_reference)
                if row["open"] >= limit_up:
                    events.append(
                        {
                            "symbol": symbol,
                            "trade_date": row["trade_date"],
                            "event_type": "LIMIT_UP_AT_OPEN",
                            "open": row["open"],
                            "previous_close": previous_close.loc[index],
                            "limit_reference_close": limit_reference,
                            "limit_price": float(limit_up),
                            "detail": f"price_limit={rule.price_limit_pct}",
                        }
                    )
                elif row["open"] <= limit_down:
                    events.append(
                        {
                            "symbol": symbol,
                            "trade_date": row["trade_date"],
                            "event_type": "LIMIT_DOWN_AT_OPEN",
                            "open": row["open"],
                            "previous_close": previous_close.loc[index],
                            "limit_reference_close": limit_reference,
                            "limit_price": float(limit_down),
                            "detail": f"price_limit={rule.price_limit_pct}",
                        }
                    )

        for row in missing.itertuples(index=False):
            events.append(
                {
                    "symbol": str(row.symbol).zfill(6),
                    "trade_date": pd.Timestamp(row.trade_date),
                    "event_type": "SUSPENDED_OR_MISSING_BAR",
                    "open": None,
                    "previous_close": None,
                    "limit_reference_close": None,
                    "limit_price": None,
                    "detail": f"calendar_gap={row.gap_length}",
                }
            )

        event_frame = pd.DataFrame(
            events,
            columns=[
                "symbol",
                "trade_date",
                "event_type",
                "open",
                "previous_close",
                "limit_reference_close",
                "limit_price",
                "detail",
            ],
        )
        event_frame = event_frame.sort_values(["symbol", "trade_date", "event_type"]).reset_index(
            drop=True
        )
        if event_frame.duplicated(["symbol", "trade_date", "event_type"]).any():
            errors.append("Trading constraint events contain duplicates")

        by_type = event_frame.groupby("event_type").size().to_dict()
        by_symbol = event_frame.groupby("symbol").size().to_dict()
        report = {
            "status": "ok" if not errors else "failed",
            "errors": errors,
            "checked_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "rules": {
                "fill_price": "next_session_open",
                "missing_bar_policy": "reject",
                "price_limit_policy": "conservative_reject",
                "settlement": "T+1",
                "lot_size": 100,
            },
            "events": len(event_frame),
            "by_type": {str(key): int(value) for key, value in by_type.items()},
            "by_symbol": {str(key): int(value) for key, value in by_symbol.items()},
        }
        self.store.save_processed(event_frame, "trading_constraint_events")
        self.store.update_constraint_database(event_frame)
        self.store.save_trading_constraint_report(report)
        if errors:
            raise ValueError("Trading constraint audit failed: " + "; ".join(errors))
        return report

    def summary(self) -> dict[str, object]:
        path = self.store.processed_dir / "trading_constraint_report.json"
        if not path.is_file():
            raise FileNotFoundError("Run 'uv run quant-execution audit' first")
        import json

        return json.loads(path.read_text(encoding="utf-8"))
