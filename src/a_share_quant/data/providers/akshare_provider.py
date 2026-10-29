"""AKShare development adapter with bounded retries for ETF endpoint outages."""

from datetime import date

import akshare as ak
import numpy as np
import pandas as pd
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from a_share_quant.data.providers.base import DailyHistoryData, ProviderFrame


class AkShareProvider:
    """Fetch free development data; not intended as the sole live-trading source."""

    def fetch_open_trade_dates(self) -> ProviderFrame:
        frame = ak.tool_trade_date_hist_sina()
        return ProviderFrame(frame=frame, source="akshare.tool_trade_date_hist_sina")

    def fetch_etf_snapshot(self) -> ProviderFrame:
        try:
            frame = ak.fund_etf_spot_em()
            return ProviderFrame(frame=frame, source="akshare.fund_etf_spot_em")
        except Exception as primary_error:
            try:
                frame = ak.fund_etf_category_sina(symbol="ETF基金")
                frame.attrs["fallback_reason"] = type(primary_error).__name__
                return ProviderFrame(frame=frame, source="akshare.fund_etf_category_sina")
            except Exception as fallback_error:
                raise RuntimeError(
                    "Both AKShare ETF endpoints failed. Keep the previous validated snapshot."
                ) from fallback_error

    @staticmethod
    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _fetch_etf_dividends(prefixed_symbol: str) -> pd.DataFrame:
        return ak.fund_etf_dividend_sina(symbol=prefixed_symbol)

    def fetch_etf_dividends(self, symbol: str, exchange: str) -> ProviderFrame:
        """Fetch cumulative cash distributions for one exchange-listed ETF."""
        prefixed_symbol = f"{'sh' if exchange == 'SSE' else 'sz'}{symbol}"
        try:
            frame = self._fetch_etf_dividends(prefixed_symbol)
        except Exception as error:
            raise RuntimeError(
                f"Unable to fetch ETF distributions for {symbol}; "
                "the previously validated action table remains unchanged."
            ) from error
        return ProviderFrame(
            frame=frame,
            source="akshare.fund_etf_dividend_sina",
        )

    @staticmethod
    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _fetch_history(
        symbol: str,
        *,
        start_date: date,
        end_date: date,
        adjust: str,
    ) -> pd.DataFrame:
        frame = ak.fund_etf_hist_em(
            symbol=symbol,
            period="daily",
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust=adjust,
        )
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            adjustment_name = adjust or "none"
            raise RuntimeError(
                f"ETF history endpoint returned no rows for {symbol} ({adjustment_name})"
            )
        return frame

    def fetch_etf_daily(
        self,
        symbol: str,
        exchange: str,
        *,
        start_date: date,
        end_date: date,
        prefer_tencent: bool = False,
    ) -> DailyHistoryData:
        """Fetch both execution prices and forward-adjusted research prices."""
        if not prefer_tencent:
            try:
                raw = self._fetch_history(
                    symbol,
                    start_date=start_date,
                    end_date=end_date,
                    adjust="",
                )
                adjusted = self._fetch_history(
                    symbol,
                    start_date=start_date,
                    end_date=end_date,
                    adjust="qfq",
                )
                source = "akshare.fund_etf_hist_em"
            except Exception:
                raw, adjusted, source = self._fetch_fallback_history(
                    symbol,
                    exchange,
                    start_date=start_date,
                    end_date=end_date,
                )
        else:
            raw, adjusted, source = self._fetch_fallback_history(
                symbol,
                exchange,
                start_date=start_date,
                end_date=end_date,
            )

        return DailyHistoryData(
            raw=raw,
            adjusted=adjusted,
            source=source,
            retrieved_at=pd.Timestamp.now(tz="Asia/Shanghai").isoformat(),
        )

    def _fetch_fallback_history(
        self,
        symbol: str,
        exchange: str,
        *,
        start_date: date,
        end_date: date,
    ) -> tuple[pd.DataFrame, pd.DataFrame, str]:
        """Use independent public endpoints when Eastmoney rejects a request."""
        prefixed_symbol = f"{'sh' if exchange == 'SSE' else 'sz'}{symbol}"
        try:
            raw_standard = ak.fund_etf_hist_sina(symbol=prefixed_symbol)
            raw_source = "akshare.fund_etf_hist_sina"
        except Exception:
            raw_standard = self._fetch_tencent_history(
                prefixed_symbol,
                start_date=start_date,
                end_date=end_date,
                adjust="",
            )
            raw_source = "akshare.stock_zh_a_hist_tx"

        adjusted_standard = self._fetch_tencent_history(
            prefixed_symbol,
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
        raw_dates = pd.to_datetime(raw_standard["date"], errors="coerce")
        adjusted_dates = pd.to_datetime(adjusted_standard["date"], errors="coerce")
        common_start = max(raw_dates.min(), adjusted_dates.min())
        common_end = min(raw_dates.max(), adjusted_dates.max())
        raw_standard = raw_standard[
            raw_dates.between(common_start, common_end)
        ].reset_index(drop=True)
        adjusted_standard = adjusted_standard[
            adjusted_dates.between(common_start, common_end)
        ].reset_index(drop=True)
        raw = self._standard_to_provider_frame(
            raw_standard,
            start_date=start_date,
            end_date=end_date,
            volume_is_shares=True,
        )
        adjusted = self._standard_to_provider_frame(
            adjusted_standard,
            start_date=start_date,
            end_date=end_date,
            volume_is_shares=True,
        )
        return raw, adjusted, f"{raw_source}+akshare.stock_zh_a_hist_tx"

    @staticmethod
    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    def _fetch_tencent_history(
        prefixed_symbol: str,
        *,
        start_date: date,
        end_date: date,
        adjust: str,
    ) -> pd.DataFrame:
        frame = ak.stock_zh_a_hist_tx(
            symbol=prefixed_symbol,
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust=adjust,
        )
        if not isinstance(frame, pd.DataFrame) or frame.empty:
            raise RuntimeError(
                f"Tencent history returned no rows for {prefixed_symbol} ({adjust or 'none'})"
            )
        return frame

    @staticmethod
    def _standard_to_provider_frame(
        frame: pd.DataFrame,
        *,
        start_date: date,
        end_date: date,
        volume_is_shares: bool,
    ) -> pd.DataFrame:
        required = {"date", "open", "high", "low", "close", "volume", "amount"}
        missing = sorted(required - set(frame.columns))
        if missing:
            raise RuntimeError(f"Fallback history response is missing columns: {missing}")

        result = frame.copy()
        result["date"] = pd.to_datetime(result["date"], errors="coerce").dt.normalize()
        result = result[
            (result["date"] >= pd.Timestamp(start_date))
            & (result["date"] <= pd.Timestamp(end_date))
        ].sort_values("date")
        result = result.drop_duplicates("date", keep="last").reset_index(drop=True)
        if result.empty:
            raise RuntimeError("Fallback history contains no rows in the requested date range")

        for column in ("open", "high", "low", "close", "volume", "amount"):
            result[column] = pd.to_numeric(result[column], errors="coerce")
        if volume_is_shares:
            result["volume"] = result["volume"] / 100

        previous_close = result["close"].shift(1)
        amplitude = (result["high"] - result["low"]) / previous_close.replace(0, np.nan) * 100
        change = result["close"].diff()
        change_pct = result["close"].pct_change(fill_method=None) * 100
        turnover_rate = (
            pd.to_numeric(result["turnover"], errors="coerce") * 100
            if "turnover" in result
            else pd.Series(np.nan, index=result.index)
        )

        return pd.DataFrame(
            {
                "日期": result["date"].dt.strftime("%Y-%m-%d"),
                "开盘": result["open"],
                "收盘": result["close"],
                "最高": result["high"],
                "最低": result["low"],
                "成交量": result["volume"],
                "成交额": result["amount"],
                "振幅": amplitude,
                "涨跌幅": change_pct,
                "涨跌额": change,
                "换手率": turnover_rate,
            }
        )

