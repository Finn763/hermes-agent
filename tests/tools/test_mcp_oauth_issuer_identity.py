"""OAuth discovery issuer identity for a path-scoped advertised authorization server (#116233).

``validate_metadata_issuer`` (RFC 8414 §3.3) requires an authorization-server metadata document's
``issuer`` to be the identifier discovery was built from. Strava's MCP resource advertises
``authorization_servers: ["https://www.strava.com/mcp-issuer"]``, and the document served from
``https://www.strava.com/.well-known/oauth-authorization-server/mcp-issuer`` answers
``"issuer": "https://www.strava.com"``. The two never compare equal, so Desktop authentication stops
at discovery with ``OAuthFlowError: Authorization server metadata issuer mismatch: ...`` and never
reaches consent or token exchange.

Hermes now applies the identity rule of ``tools.mcp_oauth_provider.issuer_identifiers_match`` — same
scheme/host/port, and a metadata document naming the bare origin of the advertised identifier counts
as the same authorization server — accepts that document itself, and leaves everything the SDK derives
from the advertised identifier (its credential binding, its discovery URLs, its RFC 9207 expectation
against the document's real ``issuer``) exactly as the SDK computed it. A *different* origin — the
mix-up the check exists for — still reaches the SDK's check and is rejected.

The SDK generator is driven with the two public Strava documents inline; no network access.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("mcp.client.auth.oauth2", reason="MCP SDK 2.x required")

from mcp.client.auth.exceptions import OAuthFlowError  # noqa: E402

from tools.mcp_oauth_device import DEVICE_GRANT, _device_metadata  # noqa: E402
from tools.mcp_oauth_provider import issuer_identifiers_match  # noqa: E402

MCP_URL = "https://mcp.strava.com/mcp"
ADVERTISED_ISSUER = "https://www.strava.com/mcp-issuer"
METADATA_ISSUER = "https://www.strava.com"

# Public shapes quoted in the issue (mcp.strava.com/.well-known/oauth-protected-resource, and
# www.strava.com/.well-known/oauth-authorization-server/mcp-issuer).
PROTECTED_RESOURCE_METADATA = {
    "resource": MCP_URL,
    "authorization_servers": [ADVERTISED_ISSUER],
}
AUTHORIZATION_SERVER_METADATA = {
    "issuer": METADATA_ISSUER,
    "authorization_endpoint": f"{METADATA_ISSUER}/oauth/authorize",
    "token_endpoint": f"{METADATA_ISSUER}/oauth/token",
    "registration_endpoint": f"{METADATA_ISSUER}/oauth/register",
    "response_types_supported": ["code"],
    "grant_types_supported": ["authorization_code", "refresh_token"],
    "code_challenge_methods_supported": ["S256"],
    "scopes_supported": ["read", "activity:read_all"],
}
_CHALLENGE = {"www-authenticate": f'Bearer resource_metadata="{MCP_URL}/.well-known/oauth-protected-resource"'}


# ---------------------------------------------------------------------------
# The identity rule itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("metadata_issuer, advertised, expected", [
    ("https://www.strava.com", "https://www.strava.com/mcp-issuer", True),      # the reported pair
    ("https://www.strava.com/", "https://www.strava.com/mcp-issuer", True),     # trailing slash
    ("https://WWW.STRAVA.COM", "https://www.strava.com/mcp-issuer", True),      # host case
    ("https://www.strava.com", "https://www.strava.com:443/mcp-issuer", True),  # explicit default port
    ("https://www.strava.com/mcp-issuer", "https://www.strava.com/mcp-issuer", True),
    ("https://www.strava.com/other", "https://www.strava.com/mcp-issuer", False),   # other path
    ("https://www.strava.com/mcp-issuer/x", "https://www.strava.com/mcp-issuer", False),
    ("https://evil.example", "https://www.strava.com/mcp-issuer", False),           # other origin
    ("https://www.strava.com.evil.example", "https://www.strava.com/mcp-issuer", False),
    ("https://mcp.strava.com", "https://www.strava.com/mcp-issuer", False),         # other host
    ("http://www.strava.com", "https://www.strava.com/mcp-issuer", False),          # scheme downgrade
    ("https://www.strava.com:8443", "https://www.strava.com/mcp-issuer", False),    # other port
    (None, ADVERTISED_ISSUER, False),
    (METADATA_ISSUER, None, False),
    ("not a url", ADVERTISED_ISSUER, False),
])
def test_issuer_identifiers_match(metadata_issuer, advertised, expected):
    assert issuer_identifiers_match(metadata_issuer, advertised) is expected


# ---------------------------------------------------------------------------
# Browser flow (the Desktop path): the SDK generator, driven through the pumps
# ---------------------------------------------------------------------------


async def _start_flow(tmp_path, monkeypatch, *, client_info_issuer=None, persisted_metadata=False):
    from pydantic import AnyUrl

    from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthMetadata

    from tools.mcp_oauth import HermesTokenStorage
    from tools.mcp_oauth_manager import _HERMES_PROVIDER_CLS, reset_manager_for_tests
    from tools.mcp_tool import sdk_httpx

    httpx = sdk_httpx()
    assert _HERMES_PROVIDER_CLS is not None, "MCP SDK OAuth support must be available"

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    reset_manager_for_tests()

    storage = HermesTokenStorage("strava")
    if persisted_metadata:  # what a previous connect persisted on disk
        storage.save_oauth_metadata(OAuthMetadata.model_validate(AUTHORIZATION_SERVER_METADATA))
    await storage.set_client_info(OAuthClientInformationFull(
        client_id="hermes-client",
        redirect_uris=[AnyUrl("http://127.0.0.1:12345/callback")],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        issuer=client_info_issuer,
    ))

    seen: dict[str, str] = {}

    async def redirect(url: str) -> None:
        seen["url"] = url

    async def callback():
        from mcp.shared.auth import AuthorizationCodeResult
        state = parse_qs(urlparse(seen["url"]).query)["state"][0]
        return AuthorizationCodeResult(code="c0de", state=state)

    provider = _HERMES_PROVIDER_CLS(
        server_name="strava",
        server_url=MCP_URL,
        client_metadata=OAuthClientMetadata(
            redirect_uris=[AnyUrl("http://127.0.0.1:12345/callback")], client_name="Hermes Agent"),
        storage=storage,
        redirect_handler=redirect,
        callback_handler=callback,
    )
    request = httpx.Request("POST", MCP_URL)
    return httpx, request, provider.async_auth_flow(request), provider


async def _discover(httpx, request, flow):
    """401 -> protected-resource metadata -> authorization-server metadata request."""
    assert await flow.__anext__() is request
    prm_request = await flow.asend(httpx.Response(401, request=request, headers=_CHALLENGE))
    assert ".well-known/oauth-protected-resource" in str(prm_request.url)
    asm_request = await flow.asend(
        httpx.Response(200, json=PROTECTED_RESOURCE_METADATA, request=prm_request))
    assert "/.well-known/oauth-authorization-server/" in str(asm_request.url)
    return asm_request


@pytest.mark.asyncio
async def test_path_scoped_advertised_issuer_completes_discovery(tmp_path, monkeypatch):
    """The reported failure: discovery used to die right here instead of reaching token exchange."""
    httpx, request, flow, provider = await _start_flow(tmp_path, monkeypatch)
    asm_request = await _discover(httpx, request, flow)

    token_request = await flow.asend(
        httpx.Response(200, json=AUTHORIZATION_SERVER_METADATA, request=asm_request))

    assert str(provider.context.auth_server_url) == ADVERTISED_ISSUER  # the SDK's identifier, untouched
    assert str(provider.context.oauth_metadata.issuer) == METADATA_ISSUER  # the document, verbatim
    assert str(token_request.url) == AUTHORIZATION_SERVER_METADATA["token_endpoint"]
    await flow.aclose()


@pytest.mark.asyncio
async def test_foreign_metadata_issuer_is_still_rejected(tmp_path, monkeypatch):
    """Same discovery URL, a document claiming another authorization server: still an error."""
    httpx, request, flow, _provider = await _start_flow(tmp_path, monkeypatch)
    asm_request = await _discover(httpx, request, flow)

    foreign = dict(AUTHORIZATION_SERVER_METADATA, issuer="https://evil.example")
    with pytest.raises(OAuthFlowError) as excinfo:
        await flow.asend(httpx.Response(200, json=foreign, request=asm_request))

    assert "issuer mismatch" in str(excinfo.value)
    assert "https://evil.example" in str(excinfo.value)
    await flow.aclose()


@pytest.mark.asyncio
async def test_credential_binding_survives_the_path_scoped_advertisement(tmp_path, monkeypatch):
    """SEP-2352: the SDK binds stored credentials to the *advertised* identifier and drops them (with
    the tokens) whenever a later connect's advertisement does not match. Accepting the document must
    therefore leave that identifier alone."""
    httpx, request, flow, provider = await _start_flow(
        tmp_path, monkeypatch, client_info_issuer=ADVERTISED_ISSUER, persisted_metadata=True)
    asm_request = await _discover(httpx, request, flow)

    assert str(provider.context.auth_server_url) == ADVERTISED_ISSUER
    assert provider.context.client_info is not None
    assert provider.context.client_info.client_id == "hermes-client"

    # ... and the flow reuses that registration instead of re-registering (which is what a dropped
    # client_info would yield: a POST to the metadata's registration_endpoint).
    token_request = await flow.asend(
        httpx.Response(200, json=AUTHORIZATION_SERVER_METADATA, request=asm_request))
    assert str(token_request.url) == AUTHORIZATION_SERVER_METADATA["token_endpoint"]
    await flow.aclose()


# ---------------------------------------------------------------------------
# Device flow (RFC 8628): the same identity rule, the same SDK check
# ---------------------------------------------------------------------------


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload, self.status_code = payload, status_code

    def json(self):
        return self._payload


class _Client:
    def __init__(self, payload, status_code=200):
        self._payload, self.status_code, self.urls = payload, status_code, []

    async def get(self, url):
        self.urls.append(url)
        return _Response(self._payload, self.status_code)


@pytest.mark.asyncio
async def test_device_metadata_accepts_the_same_identity():
    client = _Client(dict(AUTHORIZATION_SERVER_METADATA,
                          grant_types_supported=["authorization_code", "refresh_token", DEVICE_GRANT],
                          device_authorization_endpoint=f"{METADATA_ISSUER}/oauth/device"))
    metadata = await _device_metadata(client, MCP_URL, ADVERTISED_ISSUER)

    assert str(metadata.issuer) == METADATA_ISSUER
    assert client.urls == [f"{METADATA_ISSUER}/.well-known/oauth-authorization-server/mcp-issuer"]


@pytest.mark.asyncio
async def test_device_metadata_rejects_another_origin():
    client = _Client(dict(AUTHORIZATION_SERVER_METADATA, issuer="https://evil.example",
                          device_authorization_endpoint="https://evil.example/oauth/device"))
    with pytest.raises(OAuthFlowError) as excinfo:
        await _device_metadata(client, MCP_URL, ADVERTISED_ISSUER)

    assert "issuer mismatch" in str(excinfo.value)


@pytest.mark.asyncio
async def test_device_registration_binds_the_identifier_the_browser_flow_compares():
    """A device login's credentials must survive the next browser connect: the identifier recorded is
    the advertised one the SDK's browser flow compares against, not the metadata's ``issuer``."""
    from types import SimpleNamespace

    from pydantic import AnyUrl

    from mcp.shared.auth import OAuthClientMetadata, OAuthMetadata

    from tools.mcp_oauth_device import _register

    context = SimpleNamespace(
        client_metadata=OAuthClientMetadata(
            redirect_uris=[AnyUrl("http://127.0.0.1:12345/callback")],
            client_name="Hermes Agent",
            token_endpoint_auth_method="none",
        ),
        oauth_metadata=OAuthMetadata.model_validate(AUTHORIZATION_SERVER_METADATA),
        auth_server_url=ADVERTISED_ISSUER,
        client_info=None,
    )
    provider = SimpleNamespace(context=context, _coerce_client_secret_post=lambda: None)

    await _register(_Client(None), provider, {"client_id": "configured-client"})

    assert context.client_info.client_id == "configured-client"
    assert context.client_info.issuer == ADVERTISED_ISSUER
