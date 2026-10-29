from datetime import date

import pandas as pd

from a_share_quant.data.validation import validate_reference_data


def _valid_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    dates = pd.date_range("2026-01-01", "2026-12-31", freq="D")
    calendar = pd.DataFrame(
        {
            "calendar_date": dates,
            "is_open": [value.weekday() < 5 for value in dates],
        }
    )
    etf_master = pd.DataFrame(
        {
            "symbol": [f"5{value:05d}" for value in range(500)],
            "exchange": ["SSE"] * 500,
            "last_price": [1.0] * 500,
            "volume": [1000.0] * 500,
            "turnover": [10000.0] * 500,
        }
    )
    universe = pd.DataFrame({"symbol": ["500000"], "exchange": ["SSE"]})
    return calendar, etf_master, universe


def test_valid_reference_data_has_no_errors() -> None:
    calendar, etf_master, universe = _valid_frames()
    errors = validate_reference_data(
        calendar, etf_master, universe, today=date(2026, 10, 29)
    )
    assert errors == []


def test_missing_universe_symbol_is_reported() -> None:
    calendar, etf_master, universe = _valid_frames()
    universe.loc[0, "symbol"] = "159915"
    errors = validate_reference_data(
        calendar, etf_master, universe, today=date(2026, 10, 29)
    )
    assert any("missing from etf_master" in error for error in errors)

