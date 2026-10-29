"""Small read-only JSON API client with bounded retries and secret-safe errors."""

from collections.abc import Mapping
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


class ApiResponseError(RuntimeError):
    """Raised when an API returns an unsuccessful response."""


class HttpJsonClient:
    """Read-only HTTP client used by future data-provider adapters."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Accept": "application/json", "User-Agent": "a-share-etf-quant/0.1"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpJsonClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @retry(
        retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        reraise=True,
    )
    def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, str | int | float] | None = None,
    ) -> dict[str, Any] | list[Any]:
        response = self._client.get(path, params=params)
        if not response.is_success:
            raise ApiResponseError(
                f"API request failed with status {response.status_code} for path {path!r}"
            )
        payload = response.json()
        if not isinstance(payload, (dict, list)):
            raise ApiResponseError("API response must be a JSON object or array")
        return payload

