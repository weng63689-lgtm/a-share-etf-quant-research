"""Safely deploy the domestic ETF strategy to a local QuantDinger instance."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

SOURCE_NAME = "A股境内ETF多资产信号 v1"
STRATEGY_NAME = "A股境内ETF多资产模拟盘"

VARIANT_NAMES = {
    "baseline": (SOURCE_NAME, STRATEGY_NAME),
    "stop08": (
        "A股境内ETF多资产信号 v2止损8",
        "A股境内ETF多资产模拟盘 v2止损8候选",
    ),
}


class QuantDingerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )

    quantdinger_base_url: str = "http://127.0.0.1:8888"
    quantdinger_agent_token: str | None = None


@dataclass(frozen=True)
class DeploymentResult:
    source_id: int
    strategy_id: int | None
    execution_mode: str
    created_strategy: bool


def strategy_source_path() -> Path:
    return Path(__file__).with_name("quantdinger_strategy.py")


def source_payload(variant: str = "baseline") -> dict[str, object]:
    if variant not in VARIANT_NAMES:
        raise ValueError(f"Unknown QuantDinger source variant: {variant}")
    source_name, _ = VARIANT_NAMES[variant]
    code = strategy_source_path().read_text(encoding="utf-8")
    if variant == "stop08":
        marker = "POSITION_STOP_LOSS_PCT = 0.0"
        if marker not in code:
            raise ValueError("QuantDinger strategy stop-loss marker is missing")
        code = code.replace(marker, "POSITION_STOP_LOSS_PCT = 0.08", 1)
    return {
        "name": source_name,
        "description": (
            "Signal-only QuantDinger port of the frozen domestic ETF "
            f"trend and volatility-target research strategy ({variant})."
        ),
        "code": code,
        "metadata": {
            "research_source": "a-share-etf-quant",
            "execution_boundary": "signal_only",
            "universe": "20 domestic ETFs",
            "variant": variant,
        },
    }


def deployment_payload(
    source_id: int,
    *,
    initial_capital: float,
    variant: str = "baseline",
) -> dict[str, object]:
    if initial_capital <= 0:
        raise ValueError("initial capital must be positive")
    if variant not in VARIANT_NAMES:
        raise ValueError(f"Unknown QuantDinger deployment variant: {variant}")
    _, strategy_name = VARIANT_NAMES[variant]
    return {
        "name": strategy_name,
        "sourceId": int(source_id),
        "initialCapital": float(initial_capital),
        "executionMode": "signal",
        "leverageEnabled": False,
        "leverage": 1,
        "params": {},
    }


def validate_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("QuantDinger base URL must be a valid HTTP or HTTPS URL")
    if parsed.scheme == "http" and parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError("Remote QuantDinger connections must use HTTPS")
    return value.rstrip("/")


def _idempotency_key(prefix: str, payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:32]}"


class QuantDingerClient:
    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0) -> None:
        if not token:
            raise ValueError("QuantDinger agent token is required")
        self.base_url = validate_base_url(base_url)
        self._client = httpx.Client(
            base_url=f"{self.base_url}/api/agent/v1",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, object] | list[object]:
        headers = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        response = self._client.request(method, path, json=payload, headers=headers)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            detail = response.text[:1000]
            raise RuntimeError(
                f"QuantDinger request failed with HTTP {response.status_code}: {detail}"
            ) from error
        body = response.json()
        if isinstance(body, dict) and "data" in body:
            data = body["data"]
            if isinstance(data, (dict, list)):
                return data
        return body

    def whoami(self) -> dict[str, object]:
        result = self._request("GET", "/whoami")
        return result if isinstance(result, dict) else {"data": result}

    def compile_source(self, code: str) -> dict[str, object]:
        payload = {"code": code}
        result = self._request(
            "POST",
            "/strategy-sources/compile",
            payload=payload,
            idempotency_key=_idempotency_key("source-compile", payload),
        )
        return result if isinstance(result, dict) else {"manifest": result}

    def list_sources(self) -> list[dict[str, object]]:
        result = self._request("GET", "/strategy-sources?limit=200")
        if not isinstance(result, list):
            return []
        return [item for item in result if isinstance(item, dict)]

    def list_strategies(self) -> list[dict[str, object]]:
        result = self._request("GET", "/strategies?limit=200")
        if not isinstance(result, list):
            return []
        return [item for item in result if isinstance(item, dict)]

    def save_source(
        self,
        payload: dict[str, object],
        *,
        source_id: int | None,
    ) -> dict[str, object]:
        if source_id is None:
            result = self._request(
                "POST",
                "/strategy-sources",
                payload=payload,
                idempotency_key=_idempotency_key("source", payload),
            )
        else:
            update = {key: value for key, value in payload.items() if key != "name"}
            result = self._request(
                "PATCH",
                f"/strategy-sources/{source_id}",
                payload=update,
                idempotency_key=_idempotency_key(
                    "source-update",
                    {"source_id": source_id, **update},
                ),
            )
        if not isinstance(result, dict):
            raise RuntimeError("QuantDinger did not return the saved strategy source")
        return result

    def create_strategy(self, payload: dict[str, object]) -> dict[str, object]:
        result = self._request(
            "POST",
            "/strategies",
            payload=payload,
            idempotency_key=_idempotency_key("strategy", payload),
        )
        if not isinstance(result, dict):
            raise RuntimeError("QuantDinger did not return the created strategy")
        return result


def deploy(
    *,
    base_url: str,
    token: str,
    initial_capital: float,
    variant: str = "baseline",
) -> DeploymentResult:
    client = QuantDingerClient(base_url, token)
    try:
        client.whoami()
        payload = source_payload(variant)
        compile_result = client.compile_source(str(payload["code"]))
        if not compile_result.get("manifest"):
            raise RuntimeError("QuantDinger compilation returned no strategy manifest")

        existing_source = next(
            (
                item
                for item in client.list_sources()
                if item.get("name") == VARIANT_NAMES[variant][0]
            ),
            None,
        )
        source_id = int(existing_source["id"]) if existing_source else None
        saved_source = client.save_source(payload, source_id=source_id)
        resolved_source_id = int(saved_source.get("id") or source_id or 0)
        if resolved_source_id <= 0:
            raise RuntimeError("QuantDinger did not return a source ID")

        existing_strategy = next(
            (
                item
                for item in client.list_strategies()
                if item.get("strategy_name") == VARIANT_NAMES[variant][1]
            ),
            None,
        )
        if existing_strategy:
            if existing_strategy.get("execution_mode") not in {None, "signal"}:
                raise RuntimeError("Refusing to modify a non-signal QuantDinger strategy")
            return DeploymentResult(
                source_id=resolved_source_id,
                strategy_id=int(existing_strategy["id"]),
                execution_mode="signal",
                created_strategy=False,
            )

        deployment = deployment_payload(
            resolved_source_id,
            initial_capital=initial_capital,
            variant=variant,
        )
        created = client.create_strategy(deployment)
        strategy_id = int(created.get("strategy_id") or 0)
        if strategy_id <= 0:
            raise RuntimeError("QuantDinger did not return a strategy ID")
        return DeploymentResult(
            source_id=resolved_source_id,
            strategy_id=strategy_id,
            execution_mode="signal",
            created_strategy=True,
        )
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy the domestic ETF strategy to QuantDinger in signal-only mode."
    )
    parser.add_argument("--base-url", help="QuantDinger URL, default from .env")
    parser.add_argument("--initial-capital", type=float, default=500_000.0)
    parser.add_argument(
        "--variant",
        choices=sorted(VARIANT_NAMES),
        default="baseline",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the source and deployment payload without connecting",
    )
    args = parser.parse_args()
    settings = QuantDingerSettings()
    base_url = validate_base_url(args.base_url or settings.quantdinger_base_url)

    if args.dry_run:
        print(
            json.dumps(
                {
                    "base_url": base_url,
                    "source": {
                        key: value
                        for key, value in source_payload(args.variant).items()
                        if key != "code"
                    },
                    "source_characters": len(
                        str(source_payload(args.variant)["code"])
                    ),
                    "deployment": deployment_payload(
                        0,
                        initial_capital=args.initial_capital,
                        variant=args.variant,
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if not settings.quantdinger_agent_token:
        raise SystemExit(
            "QUANTDINGER_AGENT_TOKEN is not configured. "
            "Add it to .env or set it in PowerShell first."
        )
    result = deploy(
        base_url=base_url,
        token=settings.quantdinger_agent_token,
        initial_capital=args.initial_capital,
        variant=args.variant,
    )
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
