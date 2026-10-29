import pandas as pd
import pytest

from a_share_quant.data.incremental import merge_incremental_daily


def _frame(dates: list[str], adjusted: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["510300"] * len(dates),
            "trade_date": pd.to_datetime(dates),
            "open": [1.0] * len(dates),
            "high": [1.1] * len(dates),
            "low": [0.9] * len(dates),
            "close": [1.0] * len(dates),
            "adj_open": adjusted,
            "adj_high": adjusted,
            "adj_low": adjusted,
            "adj_close": adjusted,
        }
    )


def test_incremental_merge_reuses_compatible_overlap() -> None:
    existing = _frame(["2026-01-05", "2026-01-06"], [1.0, 1.01])
    recent = _frame(["2026-01-06", "2026-01-07"], [1.01, 1.02])

    merged = merge_incremental_daily(existing, recent)

    assert len(merged) == 3
    assert merged.iloc[-1]["trade_date"] == pd.Timestamp("2026-01-07")


def test_incremental_merge_requests_full_fetch_after_adjustment_rebase() -> None:
    existing = _frame(["2026-01-05", "2026-01-06"], [1.0, 1.01])
    recent = _frame(["2026-01-06", "2026-01-07"], [0.8, 0.808])

    merged = merge_incremental_daily(existing, recent)

    assert merged.empty


def test_incremental_merge_rejects_unadjusted_price_revision() -> None:
    existing = _frame(["2026-01-05", "2026-01-06"], [1.0, 1.01])
    recent = _frame(["2026-01-06", "2026-01-07"], [1.01, 1.02])
    recent.loc[0, "close"] = 1.5

    with pytest.raises(ValueError, match="unadjusted closes changed"):
        merge_incremental_daily(existing, recent)
