import json
import uuid
from copy import deepcopy
from typing import Annotated, Any

import httpx2
import pytest
from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import Field, ValidationError
from sqlalchemy import delete, select
from test_studio import clients as studio_clients
from test_studio import register

from flamoris_studio.agent_gateway import AgentTransport
from flamoris_studio.db import IntelligenceRequest, LoginSession
from flamoris_studio.intelligence import InferenceInput, configured
from flamoris_studio.intelligence_gateway import (
    INFERENCE_REQUEST_SCHEMA,
    IntelligenceError,
    IntelligenceGateway,
)

clients = studio_clients
PIN = {
    "model_id": "local",
    "provider_id": "llamacpp",
    "context_tokens": 32768,
    "max_output_tokens": 4096,
}


def configure(monkeypatch):
    monkeypatch.setenv("STUDIO_INTELLIGENCE_ENDPOINT", "http://127.0.0.1:8767/mcp")
    monkeypatch.setenv("STUDIO_INTELLIGENCE_DATA_FLOW", "approved-local")
    monkeypatch.setenv("STUDIO_INTELLIGENCE_MODELS", json.dumps([PIN]))


def body(**extra):
    return {
        "requestId": str(uuid.uuid4()),
        "modelId": "local",
        "input": "private prompt",
        **extra,
    }


class FakeGateway:
    def __init__(self):
        self.calls, self.fail, self.hook = [], False, None

    async def discover(self, models):
        return {
            "available": True,
            "models": [
                {
                    "id": "local",
                    "contextTokens": 32768,
                    "maxOutputTokens": 4096,
                    "available": True,
                }
            ],
            "capabilities": ["text.generate"],
        }

    async def execute(self, payload, *, approved_model, before_dispatch):
        await before_dispatch()
        self.calls.append(dict(payload))
        if self.hook:
            self.hook()
        if self.fail:
            raise IntelligenceError("unavailable", uncertain=True)
        return {
            "text": "<script>private answer</script>",
            "finishReason": "stop",
            "usage": None,
            "elapsedMs": 1,
            "modelId": payload["model_id"],
            "capabilityId": payload["capability_id"],
            "executionId": str(uuid.uuid4()),
        }


def install(client, gateway):
    client.app.state.intelligence_gateway_factory = lambda endpoint, token: gateway


def test_owner_isolation_durable_duplicate_and_no_prompt_store(clients, monkeypatch):
    a, b, generation, factory = clients
    ca, cb = register(a, "raw-a@example.test"), register(b, "raw-b@example.test")
    configure(monkeypatch)
    gateway = FakeGateway()
    install(a, gateway)
    request = body()
    first = a.post(
        "/api/intelligence/execute", json=request, headers={"X-CSRF-TOKEN": ca}
    )
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "private, no-store"
    request["input"] = "changed prompt"
    assert (
        a.post(
            "/api/intelligence/execute", json=request, headers={"X-CSRF-TOKEN": ca}
        ).status_code
        == 409
    )
    assert len(gateway.calls) == 1
    assert (
        b.post(
            "/api/intelligence/execute", json=request, headers={"X-CSRF-TOKEN": cb}
        ).status_code
        == 200
    )
    assert generation.submit_count == 0
    assert (
        a.get("/api/intelligence/requests/" + request["requestId"]).status_code == 404
    )
    with factory() as db:
        rows = db.scalars(select(IntelligenceRequest)).all()
        assert len(rows) == 2 and all(r.state == "completed" for r in rows)
        assert "private prompt" not in str([r.__dict__ for r in rows])
        assert "private answer" not in str([r.__dict__ for r in rows])


def test_uncertain_dispatch_never_replays_and_auth_revocation_withholds(
    clients, monkeypatch
):
    a, _, _, factory = clients
    csrf = register(a, "raw-uncertain@example.test")
    configure(monkeypatch)
    gateway = FakeGateway()
    gateway.fail = True
    install(a, gateway)
    request = body()
    response = a.post(
        "/api/intelligence/execute", json=request, headers={"X-CSRF-TOKEN": csrf}
    )
    assert response.status_code == 503 and response.json()["uncertain"] is True
    assert (
        a.post(
            "/api/intelligence/execute", json=request, headers={"X-CSRF-TOKEN": csrf}
        ).status_code
        == 409
    )
    assert len(gateway.calls) == 1
    gateway.fail = False

    def revoke():
        with factory() as db:
            db.execute(delete(LoginSession))
            db.commit()

    gateway.hook = revoke
    response = a.post(
        "/api/intelligence/execute", json=body(), headers={"X-CSRF-TOKEN": csrf}
    )
    assert response.status_code == 401 and "private answer" not in response.text
    with factory() as db:
        assert all(
            r.state == "uncertain" for r in db.scalars(select(IntelligenceRequest))
        )


def test_config_rotation_budget_and_csrf_reject(clients, monkeypatch):
    a, _, _, factory = clients
    csrf = register(a, "raw-config@example.test")
    configure(monkeypatch)
    gateway = FakeGateway()
    install(a, gateway)
    assert a.post("/api/intelligence/execute", json=body()).status_code == 403
    for extra in (
        {"modelId": "unapproved"},
        {"maxOutputTokens": 4097},
        {"input": "🐱" * 5000},
        {"temperature": "0.7"},
        {"human": "other"},
    ):
        assert (
            a.post(
                "/api/intelligence/execute",
                json=body(**extra),
                headers={"X-CSRF-TOKEN": csrf},
            ).status_code
            == 422
        )
    assert not gateway.calls
    gateway.hook = lambda: monkeypatch.setenv("STUDIO_INTELLIGENCE_TOKEN", "z" * 40)
    response = a.post(
        "/api/intelligence/execute", json=body(), headers={"X-CSRF-TOKEN": csrf}
    )
    assert response.status_code == 409 and "private answer" not in response.text
    with factory() as db:
        assert db.scalar(select(IntelligenceRequest)).state == "uncertain"


def test_input_and_config_are_exact_and_bounded(monkeypatch):
    assert (
        InferenceInput.model_validate(
            body(temperature=0, maxOutputTokens=1)
        ).temperature
        == 0
    )
    for extra in (
        {"requestId": "0" * 36},
        {"maxOutputTokens": True},
        {"input": " "},
        {"input": "🐱" * 5000},
    ):
        with pytest.raises(ValidationError):
            InferenceInput.model_validate(body(**extra))
    configure(monkeypatch)
    assert configured()[2] == [PIN]
    monkeypatch.setenv("STUDIO_INTELLIGENCE_DATA_FLOW", "remote")
    with pytest.raises(HTTPException):
        configured()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    [
        "valid",
        "partial",
        "error",
        "mismatch",
        "schema",
        "pin",
        "revoke",
        "invalid",
        "bad_discovery",
    ],
)
async def test_actual_mcp_wire_no_retry_and_exact_dispatch(mode):
    schema = deepcopy(INFERENCE_REQUEST_SCHEMA)
    if mode == "schema":
        schema["properties"]["max_output_tokens"]["type"] = "string"
    Argument = Annotated[Any, Field(json_schema_extra=schema)]
    server = MCPServer("fixture")
    calls = []
    model = {
        **PIN,
        "discovery": "configured",
        "capability_ids": ["text.generate", "reasoning.generate", "code.generate"],
    }
    if mode == "pin":
        model["context_tokens"] = 4096

    @server.tool(name="inference.execute")
    async def infer(request: Argument):
        calls.append(request)
        data = {
            "ok": True,
            "execution_id": str(uuid.uuid4()),
            "provider_id": "llamacpp",
            "model_id": "local",
            "capability_id": "text.generate",
            "elapsed_ms": 1,
            "text": "<script>safe text</script>",
            "finish_reason": "stop",
            "usage": None,
        }
        if mode == "partial":
            data.update(text="", finish_reason="length")
        if mode == "error":
            data = {
                "ok": False,
                "error": {"code": "provider_timeout", "message": "PRIVATE_DETAIL"},
            }
        if mode == "invalid":
            data["model_id"] = "another"
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(data))],
            structured_content=data,
            is_error=mode == "error",
        )

    def wire(data):
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(data))],
            structured_content=data,
        )

    @server.tool(name="models.get")
    async def get_model(model_id: str) -> dict:
        return wire(model)

    @server.tool(name="models.list")
    async def models() -> dict:
        return wire({"models": [model]})

    @server.tool(name="capabilities.list")
    async def caps() -> dict:
        if mode == "bad_discovery":
            return wire({"capabilities": [{"capability_id": []}] * 3})
        return wire(
            {"capabilities": [{"capability_id": c} for c in model["capability_ids"]]}
        )

    @server.tool(name="capabilities.get")
    async def cap(capability_id: str) -> dict:
        return wire({"capability_id": capability_id})

    if mode != "mismatch":

        @server.tool(name="system.health")
        async def health() -> dict:
            return wire(
                {
                    "healthy": True,
                    "providers": [{"provider_id": "llamacpp", "available": True}],
                }
            )

    app = server.streamable_http_app(stateless_http=True, json_response=True)
    gateway = IntelligenceGateway(
        "http://127.0.0.1:8767/mcp",
        transport_factory=lambda: AgentTransport(httpx2.ASGITransport(app=app)),
    )
    admitted = []

    async def admit():
        if mode == "revoke":
            raise HTTPException(401)
        admitted.append(True)

    request = InferenceInput.model_validate(body()).upstream()
    async with server.session_manager.run():
        if mode == "bad_discovery":
            with pytest.raises(IntelligenceError):
                await gateway.discover([PIN])
            assert not calls
        elif mode in {"valid", "partial"}:
            result = await gateway.execute(
                request, approved_model=PIN, before_dispatch=admit
            )
            assert result["finishReason"] == ("length" if mode == "partial" else "stop")
            assert len(calls) == 1 and len(admitted) == 1
            assert (await gateway.discover([PIN]))["available"]
        elif mode == "revoke":
            with pytest.raises(HTTPException):
                await gateway.execute(
                    request, approved_model=PIN, before_dispatch=admit
                )
            assert not admitted and not calls
        else:
            with pytest.raises(IntelligenceError) as error:
                await gateway.execute(
                    request, approved_model=PIN, before_dispatch=admit
                )
            assert error.value.uncertain is (mode in {"error", "invalid"})
            assert len(calls) == (1 if mode in {"error", "invalid"} else 0)
            assert "PRIVATE_DETAIL" not in str(error.value)
