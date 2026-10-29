from datetime import date

import pandas as pd

from a_share_quant.data.normalize import normalize_etf_snapshot, normalize_trade_calendar


def test_normalize_trade_calendar_builds_closed_days() -> None:
    raw = pd.DataFrame({"trade_date": [date(2026, 10, 29), date(2026, 10, 30)]})
    result = normalize_trade_calendar(
        raw,
        source="test.calendar",
        start_date="2026-10-29",
        end_date="2026-10-30",
    )

    assert result["is_open"].tolist() == [True, True]
    assert result["source"].unique().tolist() == ["test.calendar"]


def test_normalize_trade_calendar_rejects_uncovered_future() -> None:
    raw = pd.DataFrame({"trade_date": [date(2026, 10, 29)]})

    try:
        normalize_trade_calendar(raw, source="test", end_date="2026-10-30")
    except ValueError as error:
        assert "exceeds source coverage" in str(error)
    else:
        raise AssertionError("Expected uncovered calendar end to fail")


def test_normalize_sina_etf_snapshot() -> None:
    raw = pd.DataFrame(
        {
            "代码": ["sh510300", "sz159915"],
            "名称": ["沪深300ETF", "创业板ETF"],
            "最新价": [4.2, 2.1],
            "成交量": [1000, 2000],
            "成交额": [4200, 4200],
        }
    )

    result = normalize_etf_snapshot(raw, source="test.etf")

    assert result["symbol"].tolist() == ["510300", "159915"]
    assert result["exchange"].tolist() == ["SSE", "SZSE"]
    assert result["exchange_symbol"].tolist() == ["510300.SH", "159915.SZ"]

