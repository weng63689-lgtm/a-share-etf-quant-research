import httpx
import pytest

from a_share_quant.api.http_client import ApiResponseError, HttpJsonClient


def test_get_json_uses_mock_transport_without_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/calendar"
        return httpx.Response(200, json={"dates": ["2026-10-29"]})

    transport = httpx.MockTransport(handler)
    with HttpJsonClient("https://example.test", transport=transport) as client:
        payload = client.get_json("/calendar")

    assert payload == {"dates": ["2026-10-29"]}


def test_get_json_rejects_unsuccessful_status() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(401, json={"error": "denied"}))
    with (
        HttpJsonClient("https://example.test", transport=transport) as client,
        pytest.raises(ApiResponseError, match="status 401"),
    ):
        client.get_json("/private")

