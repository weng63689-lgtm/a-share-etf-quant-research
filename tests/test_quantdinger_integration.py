import ast
import importlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from a_share_quant.integrations.quantdinger import (
    STRATEGY_NAME,
    QuantDingerClient,
    _idempotency_key,
    deployment_payload,
    source_payload,
    strategy_source_path,
    validate_base_url,
)
from a_share_quant.integrations.quantdinger_status import (
    _read_dotenv,
    human_api_base,
)
from a_share_quant.integrations.quantdinger_strategy import SYMBOLS


def test_quantdinger_strategy_is_syntactically_valid_and_signal_only() -> None:
    source = strategy_source_path().read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name for node in tree.body if isinstance(node, ast.FunctionDef)
    }

    assert {"initialize", "handle_data", "rebalance"} <= functions
    assert len(SYMBOLS) == 20
    assert all(symbol.startswith("CNStock:") for symbol in SYMBOLS)
    assert "executionMode" not in source
    assert "leverageEnabled" not in source


def test_quantdinger_strategy_has_no_forbidden_sandbox_io() -> None:
    tree = ast.parse(strategy_source_path().read_text(encoding="utf-8"))
    forbidden_calls = {"open", "eval", "exec", "compile", "__import__"}
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert called_names.isdisjoint(forbidden_calls)


def test_quantdinger_deployment_is_signal_only() -> None:
    payload = deployment_payload(12, initial_capital=500_000)

    assert payload["sourceId"] == 12
    assert payload["executionMode"] == "signal"
    assert payload["leverageEnabled"] is False
    assert payload["name"] == STRATEGY_NAME


def test_quantdinger_source_payload_contains_strategy_code() -> None:
    payload = source_payload()

    assert payload["name"]
    assert len(str(payload["code"])) > 1000
    assert payload["metadata"]["execution_boundary"] == "signal_only"


def test_quantdinger_stop_loss_variant_is_separate_and_signal_only() -> None:
    source = source_payload("stop08")
    deployment = deployment_payload(
        12,
        initial_capital=500_000,
        variant="stop08",
    )

    assert "POSITION_STOP_LOSS_PCT = 0.08" in str(source["code"])
    assert source["name"] != source_payload()["name"]
    assert deployment["executionMode"] == "signal"
    assert deployment["leverageEnabled"] is False
    assert "止损8" in str(deployment["name"])


def test_quantdinger_base_url_validation() -> None:
    assert validate_base_url("http://127.0.0.1:8888/") == "http://127.0.0.1:8888"
    with pytest.raises(ValueError, match="HTTPS"):
        validate_base_url("http://example.com")


def test_quantdinger_mutating_requests_have_stable_idempotency_keys() -> None:
    first = _idempotency_key("source-compile", {"code": "abc"})
    second = _idempotency_key("source-compile", {"code": "abc"})

    assert first == second
    assert first != _idempotency_key("source-compile", {"code": "xyz"})
    assert hasattr(QuantDingerClient, "compile_source")


def test_quantdinger_source_file_is_local(tmp_path: Path) -> None:
    assert strategy_source_path().is_file()
    assert strategy_source_path().parent != tmp_path


def test_quantdinger_status_reads_local_api_port_without_exposing_secrets(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "BACKEND_PORT=127.0.0.1:5012\nADMIN_USER=test\nADMIN_PASSWORD=secret\n",
        encoding="utf-8",
    )
    values = _read_dotenv(env_file)

    assert human_api_base(values) == "http://127.0.0.1:5012"
    assert values["ADMIN_USER"] == "test"


def test_quantdinger_strategy_selects_bounded_three_etf_portfolio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    strategy = importlib.import_module(
        "a_share_quant.integrations.quantdinger_strategy"
    )
    dates = pd.date_range("2025-01-01", periods=260, freq="B")

    def history(count: int, frequency: str, field: str, symbol: str) -> pd.DataFrame:
        index = strategy.SYMBOLS.index(symbol)
        values = (
            np.linspace(1.0, 2.0, 260)
            if index < 5
            else np.linspace(2.0, 1.0, 260)
        )
        return pd.DataFrame({"close": values[-count:]}, index=dates[-count:])

    monkeypatch.setattr(
        strategy,
        "g",
        SimpleNamespace(previous_selected=[]),
        raising=False,
    )
    monkeypatch.setattr(strategy, "get_history", history, raising=False)

    weights = strategy._target_weights(SimpleNamespace(params={}))

    assert len(weights) == 3
    assert sum(weights.values()) <= 0.90 + 1e-12
    assert max(weights.values()) <= 0.35 + 1e-12
