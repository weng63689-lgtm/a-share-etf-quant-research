import pandas as pd
import pytest

from a_share_quant.data.incremental import (
    apply_cross_checked_prices,
    filter_actions_to_daily_range,
    merge_incremental_daily,
)


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
    existing["retrieved_at"] = "first"
    recent["retrieved_at"] = "second"

    merged = merge_incremental_daily(existing, recent)

    assert len(merged) == 3
    assert merged.iloc[-1]["trade_date"] == pd.Timestamp("2026-01-07")
    overlap = merged.loc[merged["trade_date"] == pd.Timestamp("2026-01-06")]
    assert overlap.iloc[0]["retrieved_at"] == "first"


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


def test_action_filter_ignores_history_before_daily_coverage() -> None:
    daily = _frame(["2026-01-05", "2026-01-06"], [1.0, 1.01])
    actions = pd.DataFrame(
        {
            "symbol": ["510300", "510300", "510300"],
            "event_date": pd.to_datetime(
                ["2011-01-01", "2026-01-05", "2027-01-01"]
            ),
        }
    )

    filtered = filter_actions_to_daily_range(actions, daily)

    assert filtered["event_date"].tolist() == [pd.Timestamp("2026-01-05")]


def test_cross_checked_prices_override_only_when_values_agree() -> None:
    dates = pd.to_datetime(["2026-01-05", "2026-01-06"])
    primary = pd.DataFrame(
        {
            "trade_date": dates,
            "open": [1.0, 1.1],
            "high": [1.1, 1.2],
            "low": [0.9, 1.0],
            "close": [1.0, 1.1],
            "adj_open": [0.8, 0.88],
            "adj_high": [0.88, 0.96],
            "adj_low": [0.72, 0.8],
            "adj_close": [0.8, 0.88],
            "source": ["existing"] * 2,
            "retrieved_at": ["old"] * 2,
        }
    )
    reference = primary.drop(columns=["source", "retrieved_at"]).copy()
    reference.loc[1, "close"] = 1.10001
    reference.loc[1, "adj_close"] = 0.880008

    merged, report = apply_cross_checked_prices(primary, reference)

    assert report["status"] == "ok"
    assert report["coverage"] == 1.0
    assert merged["source"].eq(
        "a-stock-data.tencent_kline+existing"
    ).all()

    reference.loc[1, "close"] = 1.2
    with pytest.raises(ValueError, match="price disagreement"):
        apply_cross_checked_prices(primary, reference)
