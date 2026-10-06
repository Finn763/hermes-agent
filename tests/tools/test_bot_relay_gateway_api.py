"""deliver_via_gateway_api outcome classification (review on #95741).

A dropped response AFTER an accepted POST must not look like "API unavailable":
the caller's CLI fallback would run the turn a second time. Pre-send failures
(connect refused, request rejected) stay ``None`` — safe to retry through the
subprocess transport; anything the wire swallowed after send raises
``GatewayApiDeliveryUncertain``.

All HTTP goes through an in-process ``httpx.MockTransport`` — no network.
"""
import httpx
import pytest

from tools import bot_relay


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setenv("API_SERVER_PORT", "8642")
    monkeypatch.delenv("API_SERVER_KEY", raising=False)


def _patch_client(monkeypatch, handler):
    real_client = httpx.Client

    def _factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(**kwargs)

    monkeypatch.setattr(httpx, "Client", _factory)


def _existing_bot_chat(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": [{"id": "sid-1", "title": "Bot Chat"}]})


def test_dropped_turn_response_is_uncertain_not_fallback(monkeypatch):
    def handler(request):
        if request.method == "GET":
            return _existing_bot_chat(request)
        raise httpx.ReadTimeout("simulated dropped response", request=request)

    _patch_client(monkeypatch, handler)

    with pytest.raises(bot_relay.GatewayApiDeliveryUncertain):
        bot_relay.deliver_via_gateway_api("ops", "hi", timeout=600)


def test_connect_failure_stays_fallback_safe(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    _patch_client(monkeypatch, handler)

    assert bot_relay.deliver_via_gateway_api("ops", "hi") is None


def test_rejected_turn_stays_fallback_safe(monkeypatch):
    def handler(request):
        if request.method == "GET":
            return _existing_bot_chat(request)
        return httpx.Response(400, json={"error": "message rejected"})

    _patch_client(monkeypatch, handler)

    assert bot_relay.deliver_via_gateway_api("ops", "hi") is None


def test_gateway_5xx_after_the_turn_post_is_uncertain(monkeypatch):
    def handler(request):
        if request.method == "GET":
            return _existing_bot_chat(request)
        return httpx.Response(500, json={"error": "turn crashed mid-run"})

    _patch_client(monkeypatch, handler)

    with pytest.raises(bot_relay.GatewayApiDeliveryUncertain):
        bot_relay.deliver_via_gateway_api("ops", "hi")


def test_empty_reply_is_a_delivery_not_a_fallback(monkeypatch):
    def handler(request):
        if request.method == "GET":
            return _existing_bot_chat(request)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": ""}})

    _patch_client(monkeypatch, handler)

    # A 2xx means the turn ran; "" keeps the caller from re-delivering via CLI.
    assert bot_relay.deliver_via_gateway_api("ops", "hi") == ""
