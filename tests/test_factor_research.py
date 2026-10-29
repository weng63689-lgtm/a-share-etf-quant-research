import pandas as pd
import pytest

from a_share_quant.strategy.factor_research import FactorResearchPipeline


def test_factor_metrics_detect_consistent_positive_rank_ic() -> None:
    rows = []
    for date in pd.to_datetime(["2017-01-31", "2017-02-28"]):
        for rank in range(1, 5):
            rows.append(
                {
                    "signal_date": date,
                    "momentum_120": float(rank),
                    "momentum_blend": float(rank),
                    "trend_strength": float(rank),
                    "low_volatility": float(rank),
                    "composite": float(rank),
                    "forward_20d": float(rank) / 100,
                    "forward_60d": float(rank) / 50,
                }
            )
    panel = pd.DataFrame(rows)

    detail, summary = FactorResearchPipeline._factor_metrics(panel)
    train = summary[
        (summary["period"] == "train")
        & (summary["factor"] == "momentum_120")
        & (summary["horizon"] == 20)
    ].iloc[0]

    assert len(detail) == 20
    assert train["mean_ic"] == pytest.approx(1.0)
    assert train["top_minus_bottom_positive_rate"] == 1.0


def test_negative_volume_factor_is_reoriented_consistently() -> None:
    rows = []
    for rank in range(1, 5):
        rows.append(
            {
                "signal_date": pd.Timestamp("2017-01-31"),
                "momentum_120": float(rank),
                "momentum_blend": float(rank),
                "trend_strength": float(rank),
                "low_volatility": float(rank),
                "composite": float(rank),
                "forward_20d": float(rank) / 100,
                "forward_60d": float(rank) / 50,
            }
        )

    _, summary = FactorResearchPipeline._factor_metrics(pd.DataFrame(rows))
    low_vol = summary[
        (summary["factor"] == "low_volatility") & (summary["horizon"] == 20)
    ].iloc[0]

    assert low_vol["mean_ic"] == pytest.approx(1.0)
