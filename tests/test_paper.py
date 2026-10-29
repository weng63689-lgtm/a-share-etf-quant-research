from pathlib import Path

import pandas as pd

from a_share_quant.paper.pipeline import PaperTradingPipeline
from a_share_quant.paper.store import PaperStore


def test_paper_store_state_round_trip(tmp_path: Path) -> None:
    store = PaperStore(tmp_path)
    state = {"schema_version": 1, "status": "ready", "live_trading_enabled": False}

    path = store.save_state(state)

    assert path.is_file()
    assert store.load_state() == state
    assert (tmp_path / "runs" / "paper").is_dir()


def test_paper_order_id_is_deterministic() -> None:
    row = pd.Series(
        {
            "signal_date": pd.Timestamp("2026-10-29"),
            "execution_date": pd.Timestamp("2026-10-30"),
            "symbol": "510300",
            "side": "BUY",
            "requested_quantity": 100.0,
        }
    )

    first = PaperTradingPipeline._order_id(row)
    second = PaperTradingPipeline._order_id(row)

    assert first == second
    assert len(first) == 20


def test_paper_data_fingerprint_ignores_future_rows() -> None:
    dates = pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"])
    daily = pd.DataFrame(
        {
            "symbol": ["510300"] * 3,
            "trade_date": dates,
            "close": [1.0, 1.1, 1.2],
        }
    )
    actions = pd.DataFrame(
        {
            "symbol": pd.Series(dtype=str),
            "event_date": pd.Series(dtype="datetime64[ns]"),
            "action_type": pd.Series(dtype=str),
        }
    )
    benchmark = pd.DataFrame({"trade_date": dates, "close": [4000.0, 4010.0, 4020.0]})

    original = PaperTradingPipeline._data_fingerprint(
        daily,
        actions,
        benchmark,
        through=dates[1],
    )
    extended_daily = pd.concat(
        [
            daily,
            pd.DataFrame(
                {
                    "symbol": ["510300"],
                    "trade_date": [dates[2]],
                    "close": [9.0],
                }
            ),
        ],
        ignore_index=True,
    )
    extended = PaperTradingPipeline._data_fingerprint(
        extended_daily,
        actions,
        benchmark,
        through=dates[1],
    )

    assert original == extended
