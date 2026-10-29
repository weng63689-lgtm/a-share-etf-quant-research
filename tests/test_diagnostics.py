import pandas as pd
import pytest

from a_share_quant.backtest.diagnostics import PerformanceDiagnosticsPipeline


def test_drawdown_episode_uses_prior_peak_and_recovery() -> None:
    frame = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(
                ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"]
            ),
            "equity": [100.0, 110.0, 99.0, 88.0, 112.0],
        }
    )

    episodes = PerformanceDiagnosticsPipeline._drawdown_episodes(frame)

    assert len(episodes) == 1
    assert episodes.iloc[0]["peak_date"] == pd.Timestamp("2026-01-02").date()
    assert episodes.iloc[0]["trough_date"] == pd.Timestamp("2026-01-04").date()
    assert episodes.iloc[0]["max_drawdown"] == pytest.approx(-0.2)
    assert episodes.iloc[0]["recovery_date"] == pd.Timestamp("2026-01-05").date()


def test_open_drawdown_has_no_recovery_date() -> None:
    frame = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
            "equity": [100.0, 95.0, 90.0],
        }
    )

    episodes = PerformanceDiagnosticsPipeline._drawdown_episodes(frame)

    assert episodes.iloc[0]["peak_date"] == pd.Timestamp("2026-01-01").date()
    assert episodes.iloc[0]["max_drawdown"] == pytest.approx(-0.1)
    assert pd.isna(episodes.iloc[0]["recovery_date"])
