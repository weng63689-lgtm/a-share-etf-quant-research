"""Build a separate research dataset for a broader domestic equity ETF universe."""

from pathlib import Path

import pandas as pd

from a_share_quant.accounting.actions import (
    combine_actions,
    derive_share_adjustments,
    normalize_cash_distributions,
    validate_actions,
)
from a_share_quant.data.history import (
    infer_latest_completed_session,
    normalize_etf_history,
    validate_etf_history,
)
from a_share_quant.data.providers.akshare_provider import AkShareProvider
from a_share_quant.data.storage import DataStore


class ExpandedUniversePipeline:
    def __init__(
        self,
        project_root: Path,
        *,
        universe_file: str = "research_universe_v2.csv",
        output_prefix: str = "",
        base_daily_dataset: str = "etf_daily",
        base_action_dataset: str = "etf_corporate_actions",
    ) -> None:
        self.store = DataStore(project_root)
        self.provider = AkShareProvider()
        self.universe_file = universe_file
        self.output_prefix = output_prefix
        self.base_daily_dataset = base_daily_dataset
        self.base_action_dataset = base_action_dataset

    def build(self) -> dict[str, object]:
        calendar = self.store.load_processed("trading_calendar")
        base_daily = self.store.load_processed(self.base_daily_dataset)
        base_actions = self.store.load_processed(self.base_action_dataset)
        universe = self.store.load_universe(self.universe_file)
        existing_symbols = set(base_daily["symbol"].astype(str))
        new_frames: list[pd.DataFrame] = []
        new_action_frames: list[pd.DataFrame] = []
        retrieved_at: str | None = None
        start_date = pd.Timestamp("2012-01-01").date()
        end_date = pd.Timestamp.now(tz="Asia/Shanghai").date()

        for row in universe.itertuples(index=False):
            symbol = str(row.symbol).zfill(6)
            if symbol in existing_symbols:
                continue
            history = self.provider.fetch_etf_daily(
                symbol,
                str(row.exchange),
                start_date=start_date,
                end_date=end_date,
            )
            retrieved_at = history.retrieved_at
            self.store.save_raw(
                history.raw,
                f"research_etf_daily_raw_{symbol}",
                history.source,
            )
            self.store.save_raw(
                history.adjusted,
                f"research_etf_daily_qfq_{symbol}",
                history.source,
            )
            new_frames.append(
                normalize_etf_history(
                    history.raw,
                    history.adjusted,
                    symbol=symbol,
                    exchange=str(row.exchange),
                    source=history.source,
                    retrieved_at=history.retrieved_at,
                )
            )
            dividend = self.provider.fetch_etf_dividends(symbol, str(row.exchange))
            cash = normalize_cash_distributions(
                dividend.frame,
                symbol=symbol,
                source=dividend.source,
                retrieved_at=history.retrieved_at,
            )
            if not cash.empty:
                new_action_frames.append(cash)

        daily = pd.concat(
            [base_daily, *new_frames],
            ignore_index=True,
        ).sort_values(["symbol", "trade_date"]).reset_index(drop=True)
        expected_date = infer_latest_completed_session(calendar)
        report = validate_etf_history(
            daily,
            calendar,
            universe,
            latest_expected_date=expected_date,
        )
        if report.errors:
            raise ValueError(
                "Expanded universe history validation failed: "
                + "; ".join(report.errors)
            )

        cash_actions = pd.concat(
            [base_actions[base_actions["action_type"] == "cash_distribution"], *new_action_frames],
            ignore_index=True,
        )
        share_actions = derive_share_adjustments(daily, cash_actions)
        actions = combine_actions(cash_actions, share_actions)
        action_report = validate_actions(actions, daily, universe)
        if action_report.errors:
            raise ValueError(
                "Expanded universe action validation failed: "
                + "; ".join(action_report.errors)
            )

        self.store.save_processed(
            daily,
            f"{self.output_prefix}research_etf_daily",
        )
        self.store.save_processed(
            actions,
            f"{self.output_prefix}research_etf_corporate_actions",
        )
        self.store.save_processed(
            report.coverage,
            f"{self.output_prefix}research_etf_daily_coverage",
        )
        self.store.save_processed(
            report.missing_dates,
            f"{self.output_prefix}research_etf_daily_missing_dates",
        )
        self.store.update_research_database(
            daily,
            actions,
            prefix=self.output_prefix,
        )
        action_payload = {
            "status": action_report.status,
            "rows": len(actions),
            "cash_distributions": int(
                (actions["action_type"] == "cash_distribution").sum()
            ),
            "share_adjustments": int(
                (actions["action_type"] == "share_adjustment").sum()
            ),
            "retrieved_at": retrieved_at,
        }
        self.store.save_research_action_report(action_payload, prefix=self.output_prefix)
        summary = {
            "status": "ok",
            "symbols": int(daily["symbol"].nunique()),
            "base_symbols_reused": len(existing_symbols),
            "new_symbols_downloaded": len(new_frames),
            "rows": len(daily),
            "start_date": daily["trade_date"].min().date(),
            "end_date": daily["trade_date"].max().date(),
            "missing_rows": len(report.missing_dates),
            "corporate_actions": action_payload,
        }
        self.store.save_research_history_metadata(summary, prefix=self.output_prefix)
        return summary
