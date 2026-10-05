import json
from contextlib import asynccontextmanager

import httpx2
import pytest

from flamoris_studio.gateway import GatewayError, GenerationGateway

ENDPOINT = "https://controller.example.test/api/v1/generation"
TOKEN = "fixture-controller-service-credential-32"


def connect(monkeypatch, gateway, handler):
    @asynccontextmanager
    async def connection():
        async with httpx2.AsyncClient(
            base_url=ENDPOINT + "/",
            transport=httpx2.MockTransport(handler),
            follow_redirects=False,
            trust_env=False,
        ) as http:
            yield http

    monkeypatch.setattr(gateway, "_connection", connection)


@pytest.mark.asyncio
async def test_direct_http_operations_preserve_arguments(monkeypatch):
    monkeypatch.delenv("STUDIO_GENERATION_NAMESPACE", raising=False)
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, json.loads(request.content)))
        return httpx2.Response(200, json={"ok": True})

    gateway = GenerationGateway()
    connect(monkeypatch, gateway, handler)
    names = (
        "workflows.list",
        "workflows.build",
        "jobs.submit",
        "assets.prepare",
        "assets.read",
    )
    for name in names:
        assert await gateway._call(name, {"opaque": "unchanged"}) == {"ok": True}
    assert calls == [
        ("POST", "/api/v1/generation/" + name, {"opaque": "unchanged"})
        for name in names
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome,expected",
    [
        ("timeout", "unavailable"),
        ("busy", "busy"),
        ("unknown", "unavailable"),
        ("missing_transfer", "transfer_unavailable"),
        ("redirect", "unavailable"),
        ("private_error", "upstream_failure"),
    ],
)
async def test_safe_failures_never_retry_or_fall_back(monkeypatch, outcome, expected):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if outcome == "timeout":
            raise httpx2.ReadTimeout("private upstream detail", request=request)
        if outcome == "redirect":
            return httpx2.Response(307, headers={"Location": ENDPOINT + "/jobs.submit"})
        status, code = {
            "busy": (409, "busy"),
            "unknown": (503, "submission_unknown"),
            "missing_transfer": (404, "unknown_operation"),
            "private_error": (502, "private path/input"),
        }[outcome]
        return httpx2.Response(status, json={"error": {"code": code}})

    gateway = GenerationGateway()
    connect(monkeypatch, gateway, handler)
    name = "assets.prepare" if outcome == "missing_transfer" else "jobs.submit"
    with pytest.raises(GatewayError) as error:
        await gateway._call(name)
    assert error.value.code == expected and "private" not in str(error.value)
    assert calls == ["/api/v1/generation/" + name]


@pytest.mark.asyncio
@pytest.mark.parametrize("namespace", ["generation", "other"])
async def test_mcp_namespaces_rejected_before_connection(monkeypatch, namespace):
    monkeypatch.setenv("STUDIO_GENERATION_NAMESPACE", namespace)
    gateway = GenerationGateway()

    @asynccontextmanager
    async def connection():
        pytest.fail("invalid namespace must not connect")
        yield

    monkeypatch.setattr(gateway, "_connection", connection)
    with pytest.raises(GatewayError):
        await gateway._call("system.health")


@pytest.mark.asyncio
async def test_token_is_only_sent_in_explicit_no_redirect_backend_client(monkeypatch):
    monkeypatch.setenv("STUDIO_GENERATION_ENDPOINT", ENDPOINT)
    monkeypatch.setenv("STUDIO_GENERATION_TOKEN", TOKEN)
    monkeypatch.delenv("STUDIO_GENERATION_NAMESPACE", raising=False)
    factory, seen = httpx2.AsyncClient, []

    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        assert kwargs["timeout"].read == 330
        assert kwargs["headers"] == {"Authorization": "Bearer " + TOKEN}
        seen.append(kwargs)
        return factory(**kwargs)

    monkeypatch.setattr("flamoris_studio.gateway.httpx2.AsyncClient", client)
    async with GenerationGateway()._connection() as http:
        assert str(http.base_url) == ENDPOINT + "/"
    assert len(seen) == 1 and http.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint,token",
    [
        ("https://user:password@example.test/api/v1/generation", TOKEN),
        (ENDPOINT + "?token=secret", TOKEN),
        (ENDPOINT + "#fragment", TOKEN),
        ("https://example.test:invalid/api/v1/generation", TOKEN),
        ("http://localhost:8765@other.example.test/api/v1/generation", TOKEN),
        ("http://remote.example.test:8765/api/v1/generation", TOKEN),
        ("https://example.test/mcp", TOKEN),
        (ENDPOINT + "/", TOKEN),
        (ENDPOINT, "token\r\nInjected: value"),
        (ENDPOINT, "x" * 513),
        (ENDPOINT, ""),
    ],
)
async def test_invalid_endpoint_and_header_fail_before_transport(
    monkeypatch, endpoint, token
):
    monkeypatch.setenv("STUDIO_GENERATION_ENDPOINT", endpoint)
    monkeypatch.setenv("STUDIO_GENERATION_TOKEN", token)

    def forbidden(**kwargs):
        pytest.fail("invalid endpoint/header must not construct a client")

    monkeypatch.setattr("flamoris_studio.gateway.httpx2.AsyncClient", forbidden)
    with pytest.raises(GatewayError):
        async with GenerationGateway()._connection():
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,headers,data,code",
    [
        (200, {"content-type": "image/png"}, b"x" * 11, "asset_too_large"),
        (
            200,
            {"content-type": "image/png", "content-length": "-1"},
            b"x",
            "asset_too_large",
        ),
        (200, {"content-type": "text/html"}, b"x", "upstream_failure"),
        (
            200,
            {"content-type": "image/png", "content-encoding": "gzip"},
            b"x",
            "upstream_failure",
        ),
        (
            413,
            {"content-type": "application/json"},
            b'{"error":{"code":"asset_too_large"}}',
            "asset_too_large",
        ),
    ],
)
async def test_binary_bounds_types_and_safe_limit_errors(
    monkeypatch, status, headers, data, code
):
    gateway = GenerationGateway()
    connect(
        monkeypatch,
        gateway,
        lambda request: httpx2.Response(status, headers=headers, content=data),
    )
    with pytest.raises(GatewayError) as error:
        await gateway.content("opaque", 10)
    assert error.value.code == code


@pytest.mark.asyncio
async def test_streamed_media_and_metadata_are_bounded(monkeypatch):
    class Stream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 8
            yield b"x" * 8

    gateway = GenerationGateway()
    connect(
        monkeypatch,
        gateway,
        lambda request: httpx2.Response(
            200, headers={"Content-Type": "image/png"}, stream=Stream()
        ),
    )
    with pytest.raises(GatewayError) as error:
        await gateway.content("opaque", 10)
    assert error.value.code == "asset_too_large"
    connect(
        monkeypatch,
        gateway,
        lambda request: httpx2.Response(
            200,
            content=b"x" * (2 * 1024**2 + 1),
            headers={"Content-Type": "application/json"},
        ),
    )
    with pytest.raises(GatewayError):
        await gateway._json("system.health")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [b'{"healthy":NaN}', b'{"healthy":true,"healthy":false}', b"[]"]
)
async def test_malformed_upstream_json_never_becomes_a_trusted_result(
    monkeypatch, body
):
    gateway = GenerationGateway()
    connect(
        monkeypatch,
        gateway,
        lambda request: httpx2.Response(
            200, content=body, headers={"Content-Type": "application/json"}
        ),
    )
    with pytest.raises(GatewayError) as error:
        await gateway._json("system.health")
    assert error.value.code == "upstream_failure"
