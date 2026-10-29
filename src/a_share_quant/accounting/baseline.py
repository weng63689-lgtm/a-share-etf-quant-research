"""Buy-and-hold benchmarks that exercise the account ledger end to end."""

import json
import math
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

import pandas as pd

from a_share_quant.accounting.account import Account
from a_share_quant.accounting.actions import validate_actions
from a_share_quant.accounting.actions_pipeline import CorporateActionPipeline
from a_share_quant.accounting.config import AccountConfig, load_account_config
from a_share_quant.data.storage import DataStore


@dataclass(frozen=True)
class BaselineResult:
    snapshots: pd.DataFrame
    ledger: pd.DataFrame
    summary: dict[str, object]


def _snapshot_frame(
    snapshots: list[object],
    *,
    benchmark: str,
    symbol: str,
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "benchmark": benchmark,
                "symbol": symbol,
                "trade_date": snapshot.trade_date,
                "cash": float(snapshot.cash),
                "market_value": float(snapshot.market_value),
                "equity": float(snapshot.equity),
                "total_return": float(snapshot.total_return),
                "reconciled": snapshot.reconciled,
                "positions_json": json.dumps(
                    {key: float(value) for key, value in snapshot.position_quantities},
                    sort_keys=True,
                ),
                "available_positions_json": json.dumps(
                    {key: float(value) for key, value in snapshot.available_quantities},
                    sort_keys=True,
                ),
            }
            for snapshot in snapshots
        ]
    )


def _summary(
    *,
    benchmark: str,
    symbol: str,
    initial_cash: Decimal,
    snapshots: pd.DataFrame,
    ledger: pd.DataFrame,
) -> dict[str, object]:
    equity = snapshots["equity"].astype(float)
    daily_return = equity.pct_change(fill_method=None).dropna()
    final_equity = float(equity.iloc[-1])
    start = pd.Timestamp(snapshots["trade_date"].iloc[0])
    end = pd.Timestamp(snapshots["trade_date"].iloc[-1])
    elapsed_years = max((end - start).days / 365.25, 1 / 252)
    annualized = (
        (final_equity / float(initial_cash)) ** (1 / elapsed_years) - 1
        if final_equity > 0
        else -1.0
    )
    drawdown = equity / equity.cummax() - 1
    return {
        "benchmark": benchmark,
        "symbol": symbol,
        "start_date": start.date(),
        "end_date": end.date(),
        "initial_cash": float(initial_cash),
        "final_equity": round(final_equity, 2),
        "total_return": round(final_equity / float(initial_cash) - 1, 8),
        "annualized_return": round(annualized, 8),
        "annualized_volatility": round(float(daily_return.std(ddof=0) * math.sqrt(252)), 8),
        "max_drawdown": round(float(drawdown.min()), 8),
        "ledger_entries": len(ledger),
        "company_action_entries": int(
            ledger["event_type"].isin(["CASH_DISTRIBUTION", "SHARE_ADJUSTMENT"]).sum()
        ),
    }


def _actions_for_date(actions: pd.DataFrame, event_date: pd.Timestamp) -> pd.DataFrame:
    if actions.empty:
        return actions
    return actions[actions["event_date"] == event_date]


def _apply_actions(
    account: Account,
    trade_date: pd.Timestamp,
    actions: pd.DataFrame,
) -> None:
    if actions.empty:
        return
    for row in actions[actions["action_type"] == "share_adjustment"].itertuples(index=False):
        account.apply_share_adjustment(
            trade_date,
            str(row.symbol),
            row.share_multiplier,
            note=f"derived share adjustment; {row.derivation}",
        )
    for row in actions[actions["action_type"] == "cash_distribution"].itertuples(index=False):
        account.apply_cash_distribution(
            trade_date,
            str(row.symbol),
            row.cash_per_unit,
            note=f"cash distribution; {row.derivation}",
        )


def run_single_asset_buy_hold(
    daily: pd.DataFrame,
    actions: pd.DataFrame,
    *,
    symbol: str,
    initial_cash: float,
    lot_size: int,
) -> BaselineResult:
    symbol = str(symbol).zfill(6)
    prices = daily[daily["symbol"] == symbol].sort_values("trade_date").reset_index(drop=True)
    if prices.empty:
        raise ValueError(f"No daily data for buy-and-hold symbol {symbol}")
    account = Account(initial_cash, lot_size=lot_size)
    first = prices.iloc[0]
    entry_price = Decimal(str(first["open"]))
    target_quantity = (
        account.cash / entry_price / Decimal(lot_size)
    ).to_integral_value(rounding=ROUND_FLOOR) * Decimal(lot_size)
    if target_quantity <= 0:
        raise ValueError(f"Initial cash cannot buy one lot of {symbol}")
    account.buy(
        first["trade_date"],
        symbol,
        entry_price,
        target_quantity,
        note="buy-and-hold entry",
    )

    action_frame = actions[actions["symbol"] == symbol].copy()
    snapshot_rows: list[object] = []
    for row in prices.itertuples(index=False):
        trade_date = pd.Timestamp(row.trade_date).normalize()
        _apply_actions(account, trade_date, _actions_for_date(action_frame, trade_date))
        snapshot_rows.append(account.mark_to_market(trade_date, {symbol: row.close}))

    ledger = account.ledger_frame()
    ledger.insert(0, "benchmark", f"single_{symbol}")
    snapshots = _snapshot_frame(
        snapshot_rows,
        benchmark=f"single_{symbol}",
        symbol=symbol,
    )
    summary = _summary(
        benchmark=f"single_{symbol}",
        symbol=symbol,
        initial_cash=account.initial_cash,
        snapshots=snapshots,
        ledger=ledger,
    )
    return BaselineResult(snapshots=snapshots, ledger=ledger, summary=summary)


def run_equal_weight_buy_hold(
    daily: pd.DataFrame,
    actions: pd.DataFrame,
    *,
    initial_cash: float,
    lot_size: int,
) -> BaselineResult:
    symbols = sorted(daily["symbol"].astype(str).unique())
    if not symbols:
        raise ValueError("Daily history is empty")
    start_date = max(
        pd.to_datetime(frame["trade_date"]).min()
        for _, frame in daily.groupby("symbol")
    )
    start_rows = daily[daily["trade_date"] == start_date].set_index("symbol")
    missing = [symbol for symbol in symbols if symbol not in start_rows.index]
    if missing:
        raise ValueError(f"Equal-weight start date is missing symbols: {missing}")

    account = Account(initial_cash, lot_size=lot_size)
    target_cash = account.cash / Decimal(len(symbols))
    for symbol in symbols:
        price = Decimal(str(start_rows.loc[symbol, "open"]))
        quantity = (
            target_cash / price / Decimal(lot_size)
        ).to_integral_value(rounding=ROUND_FLOOR) * Decimal(lot_size)
        if quantity <= 0:
            raise ValueError(f"Equal-weight allocation cannot buy one lot of {symbol}")
        account.buy(start_date, symbol, price, quantity, note="equal-weight entry")

    price_by_date = {
        pd.Timestamp(trade_date).normalize(): frame.set_index("symbol")["close"].to_dict()
        for trade_date, frame in daily[daily["trade_date"] >= start_date].groupby("trade_date")
    }
    benchmark_actions = actions[actions["event_date"] >= start_date].copy()
    last_prices: dict[str, float] = {}
    snapshot_rows: list[object] = []
    for trade_date in sorted(price_by_date):
        last_prices.update(
            {str(symbol): price for symbol, price in price_by_date[trade_date].items()}
        )
        _apply_actions(
            account,
            trade_date,
            _actions_for_date(benchmark_actions, trade_date),
        )
        snapshot_rows.append(account.mark_to_market(trade_date, last_prices))

    ledger = account.ledger_frame()
    ledger.insert(0, "benchmark", "equal_weight")
    snapshots = _snapshot_frame(
        snapshot_rows,
        benchmark="equal_weight",
        symbol="MULTI",
    )
    summary = _summary(
        benchmark="equal_weight",
        symbol="MULTI",
        initial_cash=account.initial_cash,
        snapshots=snapshots,
        ledger=ledger,
    )
    return BaselineResult(snapshots=snapshots, ledger=ledger, summary=summary)


class AccountModelPipeline:
    def __init__(
        self,
        project_root: Path,
        *,
        config: AccountConfig | None = None,
    ) -> None:
        self.store = DataStore(project_root)
        self.config = config or load_account_config(project_root)
        self.action_pipeline = CorporateActionPipeline(
            project_root,
            config=self.config,
        )

    def run(self) -> dict[str, object]:
        actions, action_quality = self.action_pipeline.update()
        daily = self.store.load_processed("etf_daily")
        universe = self.store.load_universe()
        results = [
            run_single_asset_buy_hold(
                daily,
                actions,
                symbol=str(row.symbol).zfill(6),
                initial_cash=self.config.initial_cash,
                lot_size=self.config.lot_size,
            )
            for row in universe.itertuples(index=False)
        ]
        results.append(
            run_equal_weight_buy_hold(
                daily,
                actions,
                initial_cash=self.config.initial_cash,
                lot_size=self.config.lot_size,
            )
        )

        snapshots = pd.concat([result.snapshots for result in results], ignore_index=True)
        ledger = pd.concat([result.ledger for result in results], ignore_index=True)
        summaries = pd.DataFrame([result.summary for result in results])
        errors: list[str] = []
        if not snapshots["reconciled"].all():
            errors.append("At least one snapshot does not reconcile assets to equity")
        if (snapshots[["cash", "market_value", "equity"]] < 0).any().any():
            errors.append("Snapshots contain negative balances")
        for benchmark, frame in snapshots.groupby("benchmark"):
            final_cash = float(frame.iloc[-1]["cash"])
            final_ledger_cash = float(
                ledger.loc[ledger["benchmark"] == benchmark].iloc[-1]["cash_balance"]
            )
            if round(final_cash, 2) != round(final_ledger_cash, 2):
                errors.append(f"Ledger cash does not reconcile for {benchmark}")

        quality = {
            "status": "ok" if not errors else "failed",
            "errors": errors,
            "checked_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "corporate_actions": action_quality,
            "benchmarks": len(summaries),
            "snapshots": len(snapshots),
            "ledger_entries": len(ledger),
        }
        self.store.save_processed(snapshots, "buy_hold_snapshots")
        self.store.save_processed(ledger, "account_ledger")
        self.store.save_processed(summaries, "buy_hold_summary")
        self.store.update_account_database(snapshots, ledger, summaries)
        self.store.save_account_quality_report(quality)
        payload = {
            "status": quality["status"],
            "updated_at": quality["checked_at"],
            "initial_cash": self.config.initial_cash,
            "lot_size": self.config.lot_size,
            "benchmarks": summaries.to_dict(orient="records"),
            "corporate_actions": action_quality,
        }
        self.store.save_buy_hold_summary(payload)
        if errors:
            raise ValueError("Account model validation failed: " + "; ".join(errors))
        return payload

    def validate(self) -> dict[str, object]:
        """Recheck stored snapshots, ledger, actions, and summaries without network calls."""
        daily = self.store.load_processed("etf_daily")
        actions = self.store.load_processed("etf_corporate_actions")
        universe = self.store.load_universe()
        snapshots = self.store.load_processed("buy_hold_snapshots")
        ledger = self.store.load_processed("account_ledger")
        summaries = self.store.load_processed("buy_hold_summary")
        errors: list[str] = []

        action_report = validate_actions(actions, daily, universe)
        errors.extend(action_report.errors)
        if snapshots.empty or ledger.empty or summaries.empty:
            errors.append("Account model output tables must not be empty")
        if not snapshots.empty:
            imbalance = (
                snapshots["cash"] + snapshots["market_value"] - snapshots["equity"]
            ).abs()
            if (imbalance > 0.01).any():
                errors.append("Stored snapshots do not satisfy cash + market value = equity")
            if not snapshots["reconciled"].all():
                errors.append("Stored snapshots contain a failed reconciliation flag")
            if (snapshots[["cash", "market_value", "equity"]] < 0).any().any():
                errors.append("Stored snapshots contain negative balances")
        if not ledger.empty:
            if (ledger["cash_balance"] < 0).any():
                errors.append("Stored ledger contains a negative cash balance")
            if (ledger["position_after"] < 0).any():
                errors.append("Stored ledger contains a negative position")
            if not ledger["event_type"].isin(
                ["BUY", "SELL", "CASH_DISTRIBUTION", "SHARE_ADJUSTMENT"]
            ).all():
                errors.append("Stored ledger contains an unsupported event type")

        for benchmark, snapshot_frame in snapshots.groupby("benchmark"):
            ledger_frame = ledger[ledger["benchmark"] == benchmark]
            summary_frame = summaries[summaries["benchmark"] == benchmark]
            if ledger_frame.empty or summary_frame.empty:
                errors.append(f"Missing ledger or summary for {benchmark}")
                continue
            final_snapshot = snapshot_frame.sort_values("trade_date").iloc[-1]
            final_ledger = ledger_frame.sort_values("sequence").iloc[-1]
            if round(float(final_snapshot["cash"]), 2) != round(
                float(final_ledger["cash_balance"]), 2
            ):
                errors.append(f"Final cash does not reconcile for {benchmark}")
            if round(float(final_snapshot["equity"]), 2) != round(
                float(summary_frame.iloc[0]["final_equity"]), 2
            ):
                errors.append(f"Final equity does not match summary for {benchmark}")

        quality = {
            "status": "ok" if not errors else "failed",
            "errors": list(dict.fromkeys(errors)),
            "checked_at": pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
            "corporate_actions": {
                "status": action_report.status,
                "errors": list(action_report.errors),
                "actions": len(actions),
            },
            "benchmarks": int(summaries["benchmark"].nunique()) if not summaries.empty else 0,
            "snapshots": len(snapshots),
            "ledger_entries": len(ledger),
        }
        self.store.save_account_quality_report(quality)
        return quality
