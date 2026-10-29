from datetime import date

import pandas as pd
import pytest

from a_share_quant.data.history import (
    infer_latest_completed_session,
    normalize_etf_history,
    validate_etf_history,
)
from a_share_quant.data.history_config import HistoryConfig
from a_share_quant.data.history_pipeline import HistoryPipeline
from a_share_quant.data.providers.base import DailyHistoryData

DATES = pd.date_range("2026-10-20", periods=5, freq="B")


def _provider_frame(*, factor: float = 1.0) -> pd.DataFrame:
    closes = pd.Series([2.0, 2.1, 2.2, 2.0, 2.1])
    return pd.DataFrame(
        {
            "日期": DATES.strftime("%Y-%m-%d"),
            "开盘": closes * 0.99 * factor,
            "收盘": closes * factor,
            "最高": closes * 1.02 * factor,
            "最低": closes * 0.98 * factor,
            "成交量": [1000, 1100, 1200, 1300, 1400],
            "成交额": [2000, 2200, 2400, 2600, 2800],
            "振幅": [4.0] * 5,
            "涨跌幅": [0.0, 5.0, 4.76, -9.09, 5.0],
            "涨跌额": [0.0, 0.1, 0.1, -0.2, 0.1],
            "换手率": [1.0] * 5,
        }
    )


def _daily() -> pd.DataFrame:
    return normalize_etf_history(
        _provider_frame(),
        _provider_frame(factor=1.1),
        symbol="510300",
        exchange="SSE",
        source="test.history",
        retrieved_at="2026-10-30T00:00:00+08:00",
    )


def _calendar() -> pd.DataFrame:
    return pd.DataFrame({"calendar_date": DATES, "is_open": True})


def _universe() -> pd.DataFrame:
    return pd.DataFrame({"symbol": ["510300"], "exchange": ["SSE"]})


def test_normalize_history_combines_execution_and_adjusted_prices() -> None:
    result = _daily()

    assert result["trade_date"].equals(pd.Series(DATES))
    assert result["adj_close"].iloc[0] == pytest.approx(2.2)
    assert result["adjustment_factor"].iloc[0] == pytest.approx(1.1)
    assert result["adjustment_method"].unique().tolist() == ["qfq"]


def test_normalize_history_rejects_date_set_mismatch() -> None:
    with pytest.raises(ValueError, match="date sets differ"):
        normalize_etf_history(
            _provider_frame(),
            _provider_frame(factor=1.1).iloc[:-1],
            symbol="510300",
            exchange="SSE",
            source="test.history",
        )


def test_valid_history_reports_no_errors() -> None:
    report = validate_etf_history(
        _daily(),
        _calendar(),
        _universe(),
        latest_expected_date=DATES[-1],
    )

    assert report.errors == ()
    assert report.coverage.iloc[0]["missing_sessions"] == 0
    assert report.coverage.iloc[0]["coverage_ratio"] == 1.0


def test_missing_session_is_reported_without_hiding_gap() -> None:
    daily = _daily().drop(index=2).reset_index(drop=True)
    report = validate_etf_history(
        daily,
        _calendar(),
        _universe(),
        latest_expected_date=DATES[-1],
        min_coverage_ratio=0.5,
        max_missing_trade_days=2,
    )

    assert len(report.missing_dates) == 1
    assert report.missing_dates.iloc[0]["trade_date"] == DATES[2]
    assert report.coverage.iloc[0]["missing_sessions"] == 1


def test_invalid_ohlc_order_fails_validation() -> None:
    daily = _daily()
    daily.loc[1, "high"] = daily.loc[1, "low"] * 0.9
    report = validate_etf_history(
        daily,
        _calendar(),
        _universe(),
        latest_expected_date=DATES[-1],
    )

    assert any("OHLC ordering" in error for error in report.errors)


def test_latest_completed_session_waits_for_close() -> None:
    calendar = pd.DataFrame(
        {
            "calendar_date": [pd.Timestamp("2026-10-29"), pd.Timestamp("2026-10-30")],
            "is_open": [True, True],
        }
    )

    before_close = infer_latest_completed_session(
        calendar,
        now=pd.Timestamp("2026-10-30 02:00", tz="Asia/Shanghai"),
    )
    after_close = infer_latest_completed_session(
        calendar,
        now=pd.Timestamp("2026-10-30 16:00", tz="Asia/Shanghai"),
    )

    assert before_close == pd.Timestamp("2026-10-29")
    assert after_close == pd.Timestamp("2026-10-30")


def test_history_pipeline_publishes_validated_data(tmp_path) -> None:
    class FakeProvider:
        def fetch_etf_daily(self, symbol, exchange, *, start_date, end_date):
            return DailyHistoryData(
                raw=_provider_frame(),
                adjusted=_provider_frame(factor=1.1),
                source="test.history",
                retrieved_at="2026-10-30T00:00:00+08:00",
            )

    (tmp_path / "data" / "processed").mkdir(parents=True)
    (tmp_path / "data" / "reference").mkdir(parents=True)
    _calendar().to_parquet(tmp_path / "data" / "processed" / "trading_calendar.parquet")
    _universe().to_csv(tmp_path / "data" / "reference" / "strategy_universe.csv", index=False)
    config = HistoryConfig(
        start_date=date(2026, 10, 1),
        min_coverage_ratio=0.98,
        max_missing_trade_days=1,
        max_missing_gap_days=1,
        max_daily_return_abs=0.22,
    )

    result = HistoryPipeline(tmp_path, provider=FakeProvider(), config=config).update_history(
        as_of=DATES[-1].date()
    )

    assert result["status"] == "ok"
    assert result["rows"] == 5
    assert (tmp_path / "data" / "processed" / "etf_daily.parquet").is_file()
    assert (tmp_path / "data" / "processed" / "history_quality_report.json").is_file()
    assert (tmp_path / "data" / "market.duckdb").is_file()
