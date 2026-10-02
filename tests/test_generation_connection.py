from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx2
import pytest
from mcp.types import CallToolResult, TextContent

from flamoris_studio.gateway import GatewayError, GenerationGateway


@pytest.mark.asyncio
@pytest.mark.parametrize("namespace", ["", "generation"])
async def test_explicit_direct_and_hub_names_preserve_arguments(monkeypatch, namespace):
    monkeypatch.setenv("STUDIO_GENERATION_NAMESPACE", namespace)
    calls = []
    expected = CallToolResult(content=[], structured_content={"ok": True})

    async def call(name, args, read_timeout_seconds):
        calls.append((name, args, read_timeout_seconds))
        return expected

    @asynccontextmanager
    async def connection():
        yield SimpleNamespace(call_tool=call)

    gateway = GenerationGateway()
    monkeypatch.setattr(gateway, "_connection", connection)
    for name in (
        "workflows.v3.list",
        "workflows.v3.build",
        "jobs.submit",
        "assets.prepare",
        "assets.read",
    ):
        assert (
            await gateway._call(name, {"opaque": "unchanged"}, timeout=45) is expected
        )
    prefix = namespace + "." if namespace else ""
    assert calls == [
        (prefix + name, {"opaque": "unchanged"}, 45)
        for name in (
            "workflows.v3.list",
            "workflows.v3.build",
            "jobs.submit",
            "assets.prepare",
            "assets.read",
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["timeout", "busy", "missing_transfer"])
async def test_namespaced_failures_are_sanitized_and_never_retry_or_fallback(
    monkeypatch, outcome
):
    monkeypatch.setenv("STUDIO_GENERATION_NAMESPACE", "generation")
    calls = []

    async def call(name, args, read_timeout_seconds):
        calls.append(name)
        if outcome == "timeout":
            raise TimeoutError("private upstream detail")
        message = (
            "busy: private job detail"
            if outcome == "busy"
            else "Unknown tool: generation.assets.prepare"
        )
        return CallToolResult(
            is_error=True, content=[TextContent(type="text", text=message)]
        )

    @asynccontextmanager
    async def connection():
        yield SimpleNamespace(call_tool=call)

    gateway = GenerationGateway()
    monkeypatch.setattr(gateway, "_connection", connection)
    name = "assets.prepare" if outcome == "missing_transfer" else "jobs.submit"
    with pytest.raises(GatewayError) as error:
        await gateway._call(name)
    assert (
        error.value.code
        == {
            "timeout": "unavailable",
            "busy": "busy",
            "missing_transfer": "transfer_unavailable",
        }[outcome]
    )
    assert "private" not in str(error.value)
    assert calls == ["generation." + name]


@pytest.mark.asyncio
async def test_invalid_namespace_rejects_before_connection(monkeypatch):
    monkeypatch.setenv("STUDIO_GENERATION_NAMESPACE", "other")
    gateway = GenerationGateway()

    @asynccontextmanager
    async def connection():
        pytest.fail("invalid namespace must not connect")
        yield

    monkeypatch.setattr(gateway, "_connection", connection)
    with pytest.raises(GatewayError):
        await gateway._call("system.health")


@pytest.mark.asyncio
async def test_private_token_is_only_sent_in_explicit_no_redirect_backend_client(
    monkeypatch,
):
    monkeypatch.setenv("STUDIO_GENERATION_ENDPOINT", "https://hub.example.test/mcp")
    monkeypatch.setenv("STUDIO_GENERATION_TOKEN", "backend-test-placeholder")
    sessions = []

    @asynccontextmanager
    async def transport(endpoint, *, http_client):
        assert endpoint == "https://hub.example.test/mcp"
        assert http_client.headers["Authorization"] == "Bearer backend-test-placeholder"
        assert not http_client.follow_redirects
        assert http_client.timeout.read == 330
        for status in (301, 307, 308):
            response = httpx2.Response(
                status,
                headers={"Location": "/another-path"},
                request=httpx2.Request("POST", endpoint),
            )
            with pytest.raises(GatewayError):
                await http_client.event_hooks["response"][0](response)
            assert response.is_closed
        sessions.append(http_client)
        yield (object(), object())

    class Session:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def initialize(self):
            pass

    monkeypatch.setattr("flamoris_studio.gateway.streamable_http_client", transport)
    monkeypatch.setattr("flamoris_studio.gateway.ClientSession", Session)
    async with GenerationGateway()._connection():
        pass
    assert len(sessions) == 1 and sessions[0].is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint,token",
    [
        ("https://user:password@example.test/mcp", "token"),
        ("https://example.test/mcp?token=secret", "token"),
        ("https://example.test:invalid/mcp", "token"),
        ("http://localhost:8765@other.example.test/mcp", "token"),
        ("http://remote.example.test:8765/mcp", "token"),
        ("https://example.test/mcp", "token\r\nInjected: value"),
        ("https://example.test/mcp", "x" * 4097),
    ],
)
async def test_invalid_endpoint_and_header_fail_before_transport(
    monkeypatch, endpoint, token
):
    monkeypatch.setenv("STUDIO_GENERATION_ENDPOINT", endpoint)
    monkeypatch.setenv("STUDIO_GENERATION_TOKEN", token)

    @asynccontextmanager
    async def transport(*args, **kwargs):
        pytest.fail("invalid endpoint/header must not connect")
        yield

    monkeypatch.setattr("flamoris_studio.gateway.streamable_http_client", transport)
    with pytest.raises(GatewayError):
        async with GenerationGateway()._connection():
            pass
