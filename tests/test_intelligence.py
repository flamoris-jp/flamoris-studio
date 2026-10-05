import json
import uuid
import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import delete, select
from test_studio import clients as studio_clients
from test_studio import register

from flamoris_studio.db import IntelligenceRequest, LoginSession
from flamoris_studio.intelligence import InferenceInput, configured
from flamoris_studio.intelligence_gateway import (
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
    monkeypatch.setenv("STUDIO_INTELLIGENCE_ENDPOINT", "http://127.0.0.1:8081")
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
@pytest.mark.parametrize("mode", ["valid", "partial", "error", "pin", "revoke", "invalid", "bad_discovery"])
async def test_shared_non_mcp_wire_no_retry_and_exact_dispatch(mode):
    calls, admitted = [], []

    async def provider(incoming):
        assert incoming.url.path in {"/health", "/v1/models", "/v1/chat/completions"}
        if incoming.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if incoming.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "other" if mode in {"pin", "bad_discovery"} else "local"}]})
        payload = json.loads(incoming.content)
        calls.append(payload)
        if mode == "error":
            raise httpx.ReadTimeout("PRIVATE_DETAIL")
        return httpx.Response(200, json={
            "model": "local",
            "choices": [{"finish_reason": "length" if mode == "partial" else "stop",
                         "message": {"role": "assistant", "content": "" if mode == "partial" else "<script>safe text</script>"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 9 if mode == "invalid" else 4},
        })

    gateway = IntelligenceGateway("http://127.0.0.1:8081", transport_factory=lambda: httpx.MockTransport(provider))

    async def admit():
        if mode == "revoke":
            raise HTTPException(401)
        admitted.append(True)

    request = InferenceInput.model_validate(body(instruction="system guidance")).upstream()
    if mode == "bad_discovery":
        assert (await gateway.discover([PIN]))["available"] is False
        assert not calls
    elif mode in {"valid", "partial"}:
        result = await gateway.execute(request, approved_model=PIN, before_dispatch=admit)
        assert result["finishReason"] == ("length" if mode == "partial" else "stop")
        assert len(calls) == len(admitted) == 1
        assert calls[0]["messages"] == [
            {"role": "system", "content": "system guidance"},
            {"role": "user", "content": "private prompt"},
        ]
        assert (await gateway.discover([PIN]))["available"]
    elif mode == "revoke":
        with pytest.raises(HTTPException):
            await gateway.execute(request, approved_model=PIN, before_dispatch=admit)
        assert not admitted and not calls
    else:
        with pytest.raises(IntelligenceError) as error:
            await gateway.execute(request, approved_model=PIN, before_dispatch=admit)
        assert error.value.uncertain is (mode in {"error", "invalid"})
        assert len(calls) == (1 if mode in {"error", "invalid"} else 0)
        assert "PRIVATE_DETAIL" not in str(error.value)


@pytest.mark.parametrize("endpoint", ["http://127.0.0.1:8767/mcp", "https://hub.example.test/mcp", "http://127.0.0.1:8081?secret=1"])
def test_old_internal_mcp_endpoints_fail_before_any_network(endpoint):
    with pytest.raises(IntelligenceError):
        IntelligenceGateway(endpoint, "x" * 40)
