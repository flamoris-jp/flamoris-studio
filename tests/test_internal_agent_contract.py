"""Real Studio -> Agent HTTP -> shared provider contract, with synthetic state/I/O.

Agent is a pinned test dependency only. Production Studio communicates over HTTP;
the owning repositories' PostgreSQL suites cover durable grants and request fences.
"""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import httpx2
import pytest
from fastapi import HTTPException
from flamoris_ai_agent.direct_execution import ApprovedExecutionClient
from flamoris_ai_agent.execution import IntelligenceError
from flamoris_ai_agent.execution_target import ApprovedTarget
from flamoris_ai_agent.http_server import HTTPSettings, create_http_app
from flamoris_ai_agent.principals import PrincipalBinding
from flamoris_ai_agent.runtime import AgentSession
from flamoris_ai_agent.scoped_service import ScopedAgentService

from flamoris_studio.agent_gateway import AgentError, AgentGateway, AgentTransport

TOKEN = "synthetic_token_" + "x" * 32
TARGET = ApprovedTarget(
    public_model_id="approved",
    provider_id="llamacpp",
    db_model_name="served-alias",
    db_provider="llama.cpp",
    data_flow="local_only",
)


class Principals:
    def __init__(self):
        self.bindings = {}
        self.revoked = set()

    def open(self, caller, keys):
        if (
            caller != "backend"
            or keys.human not in {"first", "second"}
            or (keys.agent, keys.project) != ("helper", "project")
        ):
            raise IntelligenceError("principal_unavailable")
        binding = PrincipalBinding(
            uuid4(),
            caller,
            keys,
            uuid4(),
            uuid4(),
            uuid4(),
            datetime.now(UTC) + timedelta(minutes=15),
        )
        self.bindings[str(binding.session_id)] = binding
        return binding

    def require(self, caller, session_id):
        binding = self.bindings.get(str(session_id))
        if binding is None or binding.delegator != caller or session_id in self.revoked:
            raise IntelligenceError("principal_unavailable")
        return binding


class Conversation:
    def __init__(self, binding):
        self.binding = binding
        self.turns = []
        self.closed = False

    def open(self, identity, load_previous):
        assert identity.model == "served-alias" and identity.provider == "llama.cpp"

    def start(self, policy, context):
        self.policy, self.context = policy, context
        self.conversation_id = uuid4()
        return self.conversation_id, uuid4()

    def save(self, role, text, metadata):
        self.turns.append((role, text, metadata))

    def close(self):
        self.closed = True


class Provider:
    def __init__(self):
        self.calls = []
        self.finish = "stop"
        self.model = "served-alias"

    async def handle(self, request):
        assert request.url.host == "127.0.0.1"
        payload = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, payload))
        if (request.method, request.url.path) == ("GET", "/v1/models"):
            return httpx.Response(200, json={"data": [{"id": "served-alias"}]})
        assert (request.method, request.url.path) == ("POST", "/v1/chat/completions")
        return httpx.Response(
            200,
            json={
                "model": self.model,
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "advice: " + payload["messages"][-1]["content"],
                        },
                        "finish_reason": self.finish,
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            },
        )


@asynccontextmanager
async def connection(monkeypatch):
    monkeypatch.setenv("AGENT_SETTINGS_ENABLED", "0")
    monkeypatch.setenv("STUDIO_AGENT_SETTINGS_ENABLED", "0")
    monkeypatch.setenv("INTELLIGENCE_BASE_URL", "http://127.0.0.1:8081")
    principals, provider, conversations, wire = Principals(), Provider(), [], []

    def client():
        return ApprovedExecutionClient(
            TARGET, transport=httpx.MockTransport(provider.handle)
        )

    def factory(request, binding):
        store = Conversation(binding)
        conversations.append(store)
        return AgentSession(
            client(),
            store,
            context_loader=lambda: [{"title": "Identity", "content": "trusted helper"}],
            context=request.context,
        )

    async def probe(binding):
        execution = client()
        try:
            await execution.resolve()
        finally:
            await execution.aclose()

    service = ScopedAgentService(principals, factory, probe)
    app = create_http_app(HTTPSettings(TOKEN, delegator_key="backend"), service)

    async def traced(scope, receive, send):
        assert scope["type"] == "http" and scope["path"].startswith("/api/v1/")
        wire.append((scope["method"], scope["path"]))
        await app(scope, receive, send)

    gateway = AgentGateway(
        "http://localhost:8768",
        TOKEN,
        transport_factory=lambda: AgentTransport(httpx2.ASGITransport(app=traced)),
    )
    async with app.router.lifespan_context(app):
        yield gateway, principals, provider, conversations, wire


def request(session_id, human):
    return {
        "session_id": session_id,
        "request_id": str(uuid4()),
        "text": "question from " + human,
        "context": {
            "revision": 1,
            "category": "image",
            "operation": "image.generate",
            "product_context_id": str(uuid4()),
            "draft_revision": 7,
            "draft": {"positive_prompt": "untrusted draft from " + human},
        },
    }


@pytest.mark.asyncio
async def test_real_agent_http_and_direct_provider_preserve_two_principals(monkeypatch):
    async with connection(monkeypatch) as (
        gateway,
        _principals,
        provider,
        stores,
        wire,
    ):
        answers, admitted = [], []
        for human in ("first", "second"):
            session_id, _ = await gateway.open(
                {
                    "human": human,
                    "agent": "helper",
                    "project": "project",
                }
            )
            assert (await gateway.availability(session_id))["available"]
            raw = request(session_id, human)

            async def before_dispatch(human=human):
                admitted.append(human)

            answer = await gateway.ask(raw, before_dispatch=before_dispatch)
            assert answer["request_id"] == raw["request_id"]
            assert answer["session_id"] == session_id
            assert answer["text"] == "advice: question from " + human
            assert answer["provenance"]["provider"] == "llamacpp"
            assert answer["provenance"]["model"] == "approved"
            assert "served-alias" not in json.dumps(answer)
            answers.append(answer)
        assert admitted == ["first", "second"]
        assert len({a["conversation_id"] for a in answers}) == 2
        assert [s.binding.keys.human for s in stores] == ["first", "second"]
        assert all(
            s.closed and [t[0] for t in s.turns] == ["user", "assistant"]
            for s in stores
        )
        submitted = [
            payload for method, _, payload in provider.calls if method == "POST"
        ]
        assert len(submitted) == 2
        for human, payload in zip(("first", "second"), submitted):
            messages = payload["messages"]
            assert "trusted helper" in messages[0]["content"]
            assert "untrusted draft" not in messages[0]["content"]
            context = json.loads(messages[1]["content"])
            assert messages[1]["role"] == "user"
            assert context["kind"] == "untrusted_studio_context"
            assert context["context"]["draft"]["positive_prompt"].endswith(human)
            assert messages[-1] == {"role": "user", "content": "question from " + human}
        assert {path for _, path in wire} == {
            "/api/v1/capabilities",
            "/api/v1/sessions/open",
            "/api/v1/ask-availability",
            "/api/v1/ask-scoped",
        }


@pytest.mark.asyncio
async def test_studio_admission_failure_prevents_real_agent_dispatch(monkeypatch):
    async with connection(monkeypatch) as (
        gateway,
        _principals,
        provider,
        stores,
        wire,
    ):
        sid, _ = await gateway.open(
            {"human": "first", "agent": "helper", "project": "project"}
        )
        wire.clear()

        async def refuse():
            raise HTTPException(401, "Login expired")

        with pytest.raises(HTTPException):
            await gateway.ask(request(sid, "first"), before_dispatch=refuse)
        assert wire == [("GET", "/api/v1/capabilities")]
        assert not stores and not provider.calls


@pytest.mark.asyncio
async def test_revoked_real_agent_session_never_reaches_provider(monkeypatch):
    async with connection(monkeypatch) as (
        gateway,
        principals,
        provider,
        stores,
        _wire,
    ):
        sid, _ = await gateway.open(
            {"human": "first", "agent": "helper", "project": "project"}
        )
        principals.revoked.add(sid)
        with pytest.raises(AgentError):
            await gateway.availability(sid)
        with pytest.raises(AgentError):
            await gateway.ask(request(sid, "first"))
        assert not stores and not provider.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["partial", "model"])
async def test_real_provider_failure_keeps_failed_turn_without_replay(
    monkeypatch, failure
):
    async with connection(monkeypatch) as (
        gateway,
        _principals,
        provider,
        stores,
        _wire,
    ):
        provider.finish = "length" if failure == "partial" else "stop"
        provider.model = "other" if failure == "model" else "served-alias"
        sid, _ = await gateway.open(
            {"human": "first", "agent": "helper", "project": "project"}
        )
        with pytest.raises(AgentError):
            await gateway.ask(request(sid, "first"))
        assert len(stores) == 1 and stores[0].closed
        assert [turn[0] for turn in stores[0].turns] == ["user"]
        assert len([call for call in provider.calls if call[0] == "POST"]) == 1
