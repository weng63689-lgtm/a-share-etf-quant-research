"""Provider protocols keep strategy code independent of external APIs."""

from dataclasses import dataclass
from datetime import date
from typing import Protocol

import pandas as pd


@dataclass(frozen=True)
class ProviderFrame:
    frame: pd.DataFrame
    source: str


@dataclass(frozen=True)
class DailyHistoryData:
    """Unadjusted and forward-adjusted daily frames fetched in one operation."""

    raw: pd.DataFrame
    adjusted: pd.DataFrame
    source: str
    retrieved_at: str


class ReferenceDataProvider(Protocol):
    def fetch_open_trade_dates(self) -> ProviderFrame: ...

    def fetch_etf_snapshot(self) -> ProviderFrame: ...


class DailyHistoryProvider(Protocol):
    def fetch_etf_daily(
        self,
        symbol: str,
        exchange: str,
        *,
        start_date: date,
        end_date: date,
        prefer_tencent: bool = False,
    ) -> DailyHistoryData: ...

