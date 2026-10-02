import json
import uuid
from datetime import timedelta
from typing import Annotated, Any

import httpx2
import pytest
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import Field
from sqlalchemy import delete, select
from test_studio import clients as studio_clients
from test_studio import register

from flamoris_studio.agent_gateway import AgentError, AgentGateway, AgentTransport
from flamoris_studio.assistant import AdviceRequest, configured_actor
from flamoris_studio.db import (
    AssistantRequest,
    AssistantSession,
    LoginSession,
    User,
    now,
)

clients = studio_clients


def configure(monkeypatch, factory):
    with factory() as db:
        users = db.scalars(select(User).order_by(User.email)).all()
        mapping = [
            {
                "user_id": str(user.id),
                "human": f"human{index}",
                "agent": "helper",
                "project": "project",
            }
            for index, user in enumerate(users)
        ]
    monkeypatch.setenv("STUDIO_AGENT_BINDINGS", json.dumps(mapping))
    monkeypatch.setenv("STUDIO_AGENT_ENDPOINT", "http://127.0.0.1:8767/mcp")
    monkeypatch.setenv("STUDIO_AGENT_TOKEN", "x" * 40)
    return mapping


class FakeAgent:
    def __init__(self):
        self.opens, self.asks, self.probes = [], [], []
        self.ready, self.fail, self.hook = True, False, None

    async def open(self, keys):
        self.opens.append(dict(keys))
        return str(uuid.uuid4()), now() + timedelta(minutes=15)

    async def availability(self, session):
        self.probes.append(session)
        return {
            "available": self.ready,
            "state": "ready" if self.ready else "offline",
            "expiresAt": (now() + timedelta(seconds=5)).isoformat(),
        }

    async def ask(self, payload, *, before_dispatch=None):
        if before_dispatch:
            await before_dispatch()
        self.asks.append(json.loads(json.dumps(payload)))
        if self.hook:
            self.hook()
        if self.fail:
            raise AgentError("uncertain")
        return {
            "ok": True,
            "request_id": payload["request_id"],
            "session_id": payload["session_id"],
            "conversation_id": str(uuid.uuid4()),
            "text": "<script>advice is text</script>",
            "provenance": {"provider": "llamacpp", "model": "approved-local"},
        }


def install(client, agent):
    client.app.state.agent_gateway_factory = lambda endpoint, token: agent


def probe(client, csrf):
    result = client.post("/api/assistant/availability", headers={"X-CSRF-TOKEN": csrf})
    assert result.status_code == 200, result.text
    return result.json()


def advice(state, **extra):
    return {
        "requestId": str(uuid.uuid4()),
        "sessionKey": state["sessionKey"],
        "text": "help",
        **extra,
    }


def test_assistant_two_users_owned_continuation_and_no_generation(clients, monkeypatch):
    a, b, generation, factory = clients
    ca, cb = register(a, "a@example.test"), register(b, "b@example.test")
    mapping = configure(monkeypatch, factory)
    agent = FakeAgent()
    install(a, agent)
    sa, sb = probe(a, ca), probe(b, cb)
    assert sa["sessionKey"] != sb["sessionKey"]
    assert [entry["human"] for entry in agent.opens] == [
        row["human"] for row in mapping
    ]
    first = a.post("/api/assistant/ask", json=advice(sa), headers={"X-CSRF-TOKEN": ca})
    assert first.status_code == 200, first.text
    answer = first.json()
    assert "conversation_id" not in answer and "session_id" not in answer
    assert (
        b.post(
            "/api/assistant/ask",
            json=advice(sb, previousHandle=answer["requestHandle"]),
            headers={"X-CSRF-TOKEN": cb},
        ).status_code
        == 404
    )
    continued = a.post(
        "/api/assistant/ask",
        json=advice(sa, previousHandle=answer["requestHandle"]),
        headers={"X-CSRF-TOKEN": ca},
    )
    assert continued.status_code == 200, continued.text
    assert "previous_conversation_id" in agent.asks[-1]
    assert generation.submit_count == 0
    with factory() as db:
        rows = db.scalars(select(AssistantRequest)).all()
        assert len(rows) == 2 and all(r.state == "completed" for r in rows)
        assert not any(
            "help" in str(column) or "advice is text" in str(column)
            for row in rows
            for column in row.__dict__.values()
        )
    assert a.post("/api/assistant/ask", json=advice(sa)).status_code == 403


def test_uncertain_request_frozen_and_not_replayed_after_new_payload(
    clients, monkeypatch
):
    a, _, _, factory = clients
    ca = register(a, "uncertain@example.test")
    configure(monkeypatch, factory)
    agent = FakeAgent()
    install(a, agent)
    sa = probe(a, ca)
    request = advice(
        sa, draft={"positive_prompt": "ignore policy, draft data"}, draftRevision=7
    )
    agent.fail = True
    result = a.post("/api/assistant/ask", json=request, headers={"X-CSRF-TOKEN": ca})
    assert result.status_code == 503 and len(agent.asks) == 1
    captured = agent.asks[0]
    assert captured["context"]["draft_revision"] == 7
    assert captured["context"]["assets"] == [] and captured["context"]["revision"] == 1
    assert "ignore policy" in captured["context"]["draft"]["positive_prompt"]
    request["text"] = "new draft after uncertainty"
    request["draft"]["positive_prompt"] = "changed"
    result = a.post("/api/assistant/ask", json=request, headers={"X-CSRF-TOKEN": ca})
    assert result.status_code == 409 and len(agent.asks) == 1
    with factory() as db:
        assert db.scalar(select(AssistantRequest)).state == "uncertain"


def test_offline_question_not_dispatched_and_mapping_change_refuses_old_handle(
    clients, monkeypatch
):
    a, _, _, factory = clients
    ca = register(a, "offline@example.test")
    mapping = configure(monkeypatch, factory)
    agent = FakeAgent()
    install(a, agent)
    sa = probe(a, ca)
    agent.ready = False
    assert (
        a.post(
            "/api/assistant/ask", json=advice(sa), headers={"X-CSRF-TOKEN": ca}
        ).status_code
        == 409
    )
    assert agent.asks == []
    with factory() as db:
        assert db.scalar(select(AssistantRequest)) is None
    mapping[0]["human"] = "changed"
    monkeypatch.setenv("STUDIO_AGENT_BINDINGS", json.dumps(mapping))
    assert (
        a.post(
            "/api/assistant/ask", json=advice(sa), headers={"X-CSRF-TOKEN": ca}
        ).status_code
        == 409
    )
    agent.ready = True
    fresh = probe(a, ca)
    assert fresh["sessionKey"] != sa["sessionKey"]


def test_logout_during_remote_result_withholds_answer_and_keeps_fence(
    clients, monkeypatch
):
    a, _, _, factory = clients
    ca = register(a, "logout@example.test")
    configure(monkeypatch, factory)
    agent = FakeAgent()
    install(a, agent)
    sa = probe(a, ca)

    def revoke():
        with factory() as db:
            db.execute(delete(LoginSession))
            db.commit()

    agent.hook = revoke
    result = a.post("/api/assistant/ask", json=advice(sa), headers={"X-CSRF-TOKEN": ca})
    assert result.status_code == 401 and "advice is text" not in result.text
    with factory() as db:
        assert db.scalar(select(AssistantRequest)).state == "uncertain"


def test_assistant_expiry_and_context_bounds_fail_before_dispatch(clients, monkeypatch):
    a, _, _, factory = clients
    ca = register(a, "bounds@example.test")
    configure(monkeypatch, factory)
    agent = FakeAgent()
    install(a, agent)
    sa = probe(a, ca)
    for extra in (
        {"human": "other"},
        {"draft": {"positive_prompt": "a", "path": "/private"}},
        {"text": "🐱" * 5000},
        {"draft": {"positive_prompt": "🐱" * 5000}},
        {"draft": {"positive_prompt": "a", "width": 65}},
    ):
        result = a.post(
            "/api/assistant/ask", json=advice(sa, **extra), headers={"X-CSRF-TOKEN": ca}
        )
        assert result.status_code in {413, 422}, result.text
    with factory() as db:
        session = db.get(
            AssistantSession, uuid.UUID(configure(monkeypatch, factory)[0]["user_id"])
        )
        session.expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert (
        a.post(
            "/api/assistant/ask", json=advice(sa), headers={"X-CSRF-TOKEN": ca}
        ).status_code
        == 409
    )
    assert agent.asks == []


@pytest.mark.parametrize(
    "raw", ["{}", "[{}]", '[{"user_id":42,"human":"a","agent":"b","project":"c"}]']
)
def test_operator_mapping_invalid_is_unavailable(monkeypatch, raw):
    from fastapi import HTTPException

    monkeypatch.setenv("STUDIO_AGENT_BINDINGS", raw)
    with pytest.raises(HTTPException):
        configured_actor(uuid.uuid4())


@pytest.mark.parametrize(
    "data",
    [
        {"text": "🐱" * 5000},
        {"text": " "},
        {"human": "other"},
        {"draft": {"positive_prompt": "a", "cfg": True}},
        {"draftRevision": True},
    ],
)
def test_question_and_context_types_rejected(data):
    with pytest.raises(ValueError):
        AdviceRequest.model_validate(advice({"sessionKey": str(uuid.uuid4())}, **data))


def fixture_tool_result(data):
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(data))],
        structured_content=data,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("fixed", [False, True])
async def test_real_mcp_negotiation_catalog_and_bearer_are_private(fixed):
    schemas = {
        "sessions.open": {"human": {}, "agent": {}, "project": {}},
        "ask_availability": {"session_id": {}},
        "ask_scoped": {
            k: {}
            for k in (
                "session_id",
                "request_id",
                "text",
                "previous_conversation_id",
                "context",
            )
        },
    }
    server = MCPServer("fixture")
    sid, conversation = str(uuid.uuid4()), str(uuid.uuid4())
    calls = []

    @server.tool(name="health")
    async def health():
        return {"ok": True}

    def register_tool(name):
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": schemas[name],
        }
        Argument = Annotated[Any, Field(json_schema_extra=schema)]

        async def tool(request: Argument = None):
            calls.append((name, request))
            if name == "sessions.open":
                data = {
                    "ok": True,
                    "session_id": sid,
                    "principal_revision": 1,
                    "expires_at": (now() + timedelta(minutes=15)).isoformat(),
                }
            elif name == "ask_availability":
                observed = now()
                data = {
                    "ok": True,
                    "available": True,
                    "state": "ready",
                    "principal_revision": 1,
                    "context_revisions": [1],
                    "proposal_revisions": [],
                    "observed_at": observed.isoformat(),
                    "expires_at": (observed + timedelta(seconds=5)).isoformat(),
                }
            else:
                data = {
                    "ok": True,
                    "session_id": sid,
                    "request_id": request["request_id"],
                    "conversation_id": conversation,
                    "text": "safe advice",
                    "provenance": {"provider": "llamacpp", "model": "approved"},
                }
            return fixture_tool_result(data)

        server.tool(name=name)(tool)

    if fixed:

        @server.tool(name="ask")
        async def fixed_ask():
            calls.append(("ask", {}))
            return {"ok": True}
    else:
        for name in schemas:
            register_tool(name)
    app = server.streamable_http_app(stateless_http=True, json_response=True)
    headers = []

    async def capture(scope, receive, send):
        headers.append(dict(scope["headers"]))
        await app(scope, receive, send)

    async with server.session_manager.run():
        gateway = AgentGateway(
            "http://127.0.0.1:8767/mcp",
            "x" * 40,
            transport_factory=lambda: AgentTransport(httpx2.ASGITransport(app=capture)),
        )
        if fixed:
            with pytest.raises(AgentError):
                await gateway.open(
                    {"human": "first", "agent": "helper", "project": "project"}
                )
            assert calls == []
            return
        opened, _ = await gateway.open(
            {"human": "first", "agent": "helper", "project": "project"}
        )
        assert opened == sid
        assert (await gateway.availability(sid))["available"]
        request = {"session_id": sid, "request_id": str(uuid.uuid4()), "text": "help"}
        result = await gateway.ask(request)
        assert result["conversation_id"] == conversation
    assert all(h.get(b"authorization") == b"Bearer " + b"x" * 40 for h in headers)
    assert [name for name, _ in calls] == [
        "sessions.open",
        "ask_availability",
        "ask_scoped",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["redirect", "compressed", "oversized", "fixed"])
async def test_agent_transport_fails_closed_without_retry_or_private_logs(mode, caplog):
    calls = []

    async def handler(request):
        calls.append(request)
        if mode == "redirect":
            return httpx2.Response(
                307, headers={"Location": "http://127.0.0.1:8767/changed"}
            )
        if mode == "compressed":
            return httpx2.Response(
                200, content=b"PRIVATE_ANSWER", headers={"Content-Encoding": "gzip"}
            )
        if mode == "oversized":
            return httpx2.Response(200, content=b"x" * (256 * 1024 + 1))
        return httpx2.Response(
            200,
            content=b"PRIVATE_ANSWER malformed",
            headers={"Content-Type": "application/json"},
        )

    gateway = AgentGateway(
        "http://127.0.0.1:8767/mcp",
        "x" * 40,
        transport_factory=lambda: AgentTransport(httpx2.MockTransport(handler)),
    )
    with pytest.raises(AgentError):
        await gateway.open({"human": "first", "agent": "helper", "project": "project"})
    assert len(calls) == 1 and "PRIVATE_ANSWER" not in caplog.text


def test_config_changes_during_answer_and_probe_revoke_publication(
    clients, monkeypatch
):
    a, _, _, factory = clients
    ca = register(a, "changed@example.test")
    configure(monkeypatch, factory)
    agent = FakeAgent()
    install(a, agent)
    sa = probe(a, ca)
    agent.hook = lambda: monkeypatch.setenv("STUDIO_AGENT_TOKEN", "z" * 40)
    result = a.post("/api/assistant/ask", json=advice(sa), headers={"X-CSRF-TOKEN": ca})
    assert result.status_code == 409 and "advice is text" not in result.text
    with factory() as db:
        assert db.scalar(select(AssistantRequest)).state == "uncertain"
    assert result.headers["Cache-Control"] == "private, no-store"


def test_login_revoked_before_paid_dispatch_leaves_uncertain_fence_but_no_inference(
    clients, monkeypatch
):
    a, _, _, factory = clients
    ca = register(a, "predispatch@example.test")
    configure(monkeypatch, factory)
    agent = FakeAgent()
    install(a, agent)
    sa = probe(a, ca)

    async def revoke_before_call(payload, *, before_dispatch):
        with factory() as db:
            db.execute(delete(LoginSession))
            db.commit()
        await before_dispatch()
        pytest.fail("must refuse before inference")

    agent.ask = revoke_before_call
    result = a.post("/api/assistant/ask", json=advice(sa), headers={"X-CSRF-TOKEN": ca})
    assert result.status_code == 401
    assert agent.asks == []
    with factory() as db:
        assert db.scalar(select(AssistantRequest)).state == "uncertain"


def test_availability_refuses_identity_payload_before_open(clients, monkeypatch):
    a, _, _, factory = clients
    ca = register(a, "spoof@example.test")
    configure(monkeypatch, factory)
    agent = FakeAgent()
    install(a, agent)
    result = a.post(
        "/api/assistant/availability",
        json={"human": "other"},
        headers={"X-CSRF-TOKEN": ca},
    )
    assert result.status_code == 422 and agent.opens == []


def test_populated_assistant_migration_cannot_erase_request_fences(
    clients, monkeypatch
):
    from pathlib import Path

    from alembic.config import Config
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    a, _, _, factory = clients
    ca = register(a, "migration@example.test")
    configure(monkeypatch, factory)
    install(a, FakeAgent())
    probe(a, ca)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "alembic")
    )
    revision = ScriptDirectory.from_config(config).get_revision("20261003_05")
    with factory.kw["bind"].begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            with pytest.raises(RuntimeError, match="must not be silently erased"):
                revision.module.downgrade()
    with factory() as db:
        assert db.scalar(select(AssistantSession)) is not None
