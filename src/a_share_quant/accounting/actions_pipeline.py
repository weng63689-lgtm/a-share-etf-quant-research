"""Fetch distributions, infer share changes, and publish validated actions."""

from pathlib import Path

import pandas as pd

from a_share_quant.accounting.actions import (
    combine_actions,
    derive_share_adjustments,
    empty_actions,
    normalize_cash_distributions,
    validate_actions,
)
from a_share_quant.accounting.config import AccountConfig, load_account_config
from a_share_quant.data.providers.akshare_provider import AkShareProvider
from a_share_quant.data.storage import DataStore


class CorporateActionPipeline:
    def __init__(
        self,
        project_root: Path,
        *,
        provider: AkShareProvider | None = None,
        config: AccountConfig | None = None,
    ) -> None:
        self.store = DataStore(project_root)
        self.provider = provider or AkShareProvider()
        self.config = config or load_account_config(project_root)

    def update(self) -> tuple[pd.DataFrame, dict[str, object]]:
        daily = self.store.load_processed("etf_daily")
        universe = self.store.load_universe()
        retrieved_at = pd.Timestamp.now(tz="Asia/Shanghai").isoformat()
        cash_frames: list[pd.DataFrame] = []

        for row in universe.itertuples(index=False):
            symbol = str(row.symbol).zfill(6)
            result = self.provider.fetch_etf_dividends(symbol, str(row.exchange))
            raw = result.frame.copy()
            if raw.empty and not len(raw.columns):
                raw = pd.DataFrame({"_empty": pd.Series(dtype=bool)})
            self.store.save_raw(raw, f"etf_dividends_{symbol}", result.source)
            cash = normalize_cash_distributions(
                result.frame,
                symbol=symbol,
                source=result.source,
                retrieved_at=retrieved_at,
            )
            if not cash.empty:
                symbol_dates = daily.loc[daily["symbol"] == symbol, "trade_date"]
                cash = cash[
                    (cash["event_date"] >= symbol_dates.min())
                    & (cash["event_date"] <= symbol_dates.max())
                ]
                cash_frames.append(cash)

        cash_actions = (
            pd.concat(cash_frames, ignore_index=True) if cash_frames else empty_actions()
        )
        share_actions = derive_share_adjustments(
            daily,
            cash_actions,
            return_gap_threshold=self.config.share_adjustment_return_gap,
        )
        actions = combine_actions(cash_actions, share_actions)
        report = validate_actions(actions, daily, universe)
        quality = {
            "status": report.status,
            "errors": list(report.errors),
            "retrieved_at": retrieved_at,
            "actions": len(actions),
            "cash_distributions": int(
                (actions["action_type"] == "cash_distribution").sum()
            )
            if not actions.empty
            else 0,
            "share_adjustments": int(
                (actions["action_type"] == "share_adjustment").sum()
            )
            if not actions.empty
            else 0,
            "by_symbol": actions.groupby("symbol").size().to_dict() if not actions.empty else {},
        }
        self.store.save_processed(actions, "etf_corporate_actions")
        self.store.save_corporate_action_report(quality)
        if report.errors:
            raise ValueError("Corporate action validation failed: " + "; ".join(report.errors))
        self.store.update_corporate_actions_database(actions)
        return actions, quality
