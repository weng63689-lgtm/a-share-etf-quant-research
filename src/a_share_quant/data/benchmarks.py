"""CSI300 benchmark data ingestion for strategy and portfolio comparison."""

from datetime import date
from pathlib import Path

import akshare as ak
import pandas as pd
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from a_share_quant.data.storage import DataStore


class BenchmarkPipeline:
    def __init__(self, project_root: Path) -> None:
        self.store = DataStore(project_root)

    @staticmethod
    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _fetch() -> pd.DataFrame:
        frame = ak.stock_zh_index_daily(symbol="sh000300")
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise RuntimeError("CSI300 benchmark endpoint returned no rows")
        return frame

    def update(self, *, start_date: date | None = None) -> dict[str, object]:
        raw = self._fetch()
        raw_path = self.store.save_raw(
            raw,
            "benchmark_csi300",
            "akshare.stock_zh_index_daily",
        )
        frame = raw.copy()
        frame["trade_date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
        frame["benchmark"] = "CSI300"
        for column in ("open", "high", "low", "close"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if start_date is not None:
            frame = frame[frame["trade_date"] >= pd.Timestamp(start_date)]
        frame = (
            frame.dropna(subset=["trade_date", "close"])
            .drop_duplicates("trade_date", keep="last")
            .sort_values("trade_date")
            .reset_index(drop=True)
        )
        if frame.empty or (frame["close"] <= 0).any():
            raise ValueError("CSI300 benchmark data is empty or contains invalid closes")
        frame["source"] = "akshare.stock_zh_index_daily"
        frame["retrieved_at"] = pd.Timestamp.now(tz="Asia/Shanghai").isoformat()
        output = frame[
            [
                "benchmark",
                "trade_date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "source",
                "retrieved_at",
            ]
        ]
        processed_path = self.store.save_processed(output, "benchmark_daily")
        self.store.update_benchmark_database(output)
        return {
            "status": "ok",
            "rows": len(output),
            "start_date": output["trade_date"].min().date(),
            "end_date": output["trade_date"].max().date(),
            "raw_path": str(raw_path),
            "processed_path": str(processed_path),
        }
