"""ETF daily prices adapted from the A-Stock-Data Tencent K-line endpoint."""

from datetime import date, timedelta

import httpx
import numpy as np
import pandas as pd

from a_share_quant.data.providers.base import DailyHistoryData

TENCENT_KLINE_HOSTS = (
    "https://web.ifzq.gtimg.cn",
    "https://proxy.finance.qq.com/ifzqgtimg",
    "https://ifzq.gtimg.cn",
)
KLINE_PATH = "/appstock/app/fqkline/get"
MAX_WINDOW_DAYS = 700


class AStockDataProvider:
    """Fetch unadjusted and forward-adjusted ETF prices through A-Stock-Data logic.

    This adapter follows the Tencent K-line strategy documented by the
    Apache-2.0 licensed A-Stock-Data project. Turnover amount is intentionally
    not synthesized here; the project keeps its existing amount source.
    """

    def __init__(self, *, timeout: float = 20.0) -> None:
        self.timeout = timeout

    @staticmethod
    def _market_symbol(symbol: str, exchange: str) -> str:
        normalized = str(symbol).zfill(6)
        prefix = "sh" if exchange == "SSE" else "sz" if exchange == "SZSE" else ""
        if not prefix:
            raise ValueError(f"Unsupported ETF exchange: {exchange}")
        return f"{prefix}{normalized}"

    def _fetch_segment(
        self,
        market_symbol: str,
        *,
        start_date: date,
        end_date: date,
        adjust: str,
    ) -> tuple[list[list[object]], str]:
        parameter = (
            f"{market_symbol},day,{start_date.isoformat()},"
            f"{end_date.isoformat()},640,{adjust}"
        )
        errors: list[str] = []
        with httpx.Client(
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://gu.qq.com/",
            },
            timeout=self.timeout,
        ) as client:
            for host in TENCENT_KLINE_HOSTS:
                try:
                    response = client.get(
                        host + KLINE_PATH,
                        params={"param": parameter},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    data = payload.get("data", {}).get(market_symbol, {})
                    if not isinstance(data, dict):
                        raise RuntimeError("response symbol node is not an object")
                    key = f"{adjust}day"
                    rows = data.get(key, data.get("day"))
                    if not isinstance(rows, list):
                        raise RuntimeError(f"response has no {key}/day rows")
                    return rows, host
                except Exception as error:
                    errors.append(f"{host}: {type(error).__name__}")
        raise RuntimeError(
            "A-Stock-Data Tencent K-line endpoints are unavailable: "
            + "; ".join(errors)
        )

    def fetch_etf_daily(
        self,
        symbol: str,
        exchange: str,
        *,
        start_date: date,
        end_date: date,
        prefer_tencent: bool = False,
    ) -> DailyHistoryData:
        del prefer_tencent
        market_symbol = self._market_symbol(symbol, exchange)
        raw, raw_source = self._fetch_history(
            market_symbol,
            start_date=start_date,
            end_date=end_date,
            adjust="",
        )
        adjusted, adjusted_source = self._fetch_history(
            market_symbol,
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
        return DailyHistoryData(
            raw=raw,
            adjusted=adjusted,
            source=f"a-stock-data.tencent_kline.{raw_source}",
            retrieved_at=pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        )

    def _fetch_history(
        self,
        market_symbol: str,
        *,
        start_date: date,
        end_date: date,
        adjust: str,
    ) -> tuple[pd.DataFrame, str]:
        if start_date > end_date:
            raise ValueError("start_date must not be after end_date")
        rows: list[list[object]] = []
        hosts: set[str] = set()
        cursor = start_date
        while cursor <= end_date:
            stop = min(cursor + timedelta(days=MAX_WINDOW_DAYS - 1), end_date)
            segment_rows, source = self._fetch_segment(
                market_symbol,
                start_date=cursor,
                end_date=stop,
                adjust=adjust,
            )
            rows.extend(segment_rows)
            hosts.add(source)
            cursor = stop + timedelta(days=1)
        if not rows:
            raise RuntimeError(f"A-Stock-Data returned no ETF rows for {market_symbol}")
        frame = self._provider_frame(rows)
        return frame, " | ".join(sorted(hosts))

    @staticmethod
    def _provider_frame(rows: list[list[object]]) -> pd.DataFrame:
        records: list[dict[str, object]] = []
        seen: set[pd.Timestamp] = set()
        for item in rows:
            if len(item) < 6:
                raise RuntimeError("A-Stock-Data K-line row has fewer than six fields")
            trade_date = pd.Timestamp(str(item[0])).normalize()
            if trade_date in seen:
                raise RuntimeError(f"A-Stock-Data returned duplicate date {trade_date.date()}")
            seen.add(trade_date)
            values = pd.to_numeric(
                pd.Series([item[1], item[2], item[3], item[4], item[5]]),
                errors="coerce",
            )
            if values.isna().any():
                raise RuntimeError(
                    f"A-Stock-Data returned invalid OHLCV for {trade_date.date()}"
                )
            records.append(
                {
                    "日期": trade_date.strftime("%Y-%m-%d"),
                    "开盘": float(values.iloc[0]),
                    "收盘": float(values.iloc[1]),
                    "最高": float(values.iloc[2]),
                    "最低": float(values.iloc[3]),
                    "成交量": float(values.iloc[4]),
                    "成交额": np.nan,
                    "振幅": np.nan,
                    "涨跌幅": np.nan,
                    "涨跌额": np.nan,
                    "换手率": np.nan,
                }
            )
        frame = pd.DataFrame(records).sort_values("日期").reset_index(drop=True)
        previous_close = frame["收盘"].shift(1)
        frame["振幅"] = (frame["最高"] - frame["最低"]) / previous_close * 100
        frame["涨跌额"] = frame["收盘"].diff()
        frame["涨跌幅"] = frame["收盘"].pct_change(fill_method=None) * 100
        if (frame[["开盘", "收盘", "最高", "最低"]] <= 0).any().any():
            raise RuntimeError("A-Stock-Data returned a non-positive ETF price")
        return frame
