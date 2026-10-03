import uuid

import pytest
from sqlalchemy import delete
from test_assistant import FakeAgent, advice, configure, install, probe
from test_studio import clients, register

from flamoris_studio.agent_gateway import AgentError
from flamoris_studio.db import LoginSession

__all__ = ["clients"]


class SettingsAgent(FakeAgent):
    def __init__(self):
        super().__init__()
        self.bound = {}
        self.saved = []
        self.reads = []
        self.conflict = False
        self.read_hook = None

    async def models(self, keys):
        return {
            "models": [
                {"id": "local", "display_name": "Internal", "data_flow": "local_only"},
                {
                    "id": "api",
                    "display_name": "OpenAI",
                    "data_flow": "remote_authorized",
                },
            ],
            "defaultModelId": "local",
        }

    async def open(self, keys):
        sid, expiry = await super().open(keys)
        self.bound[sid] = keys["human"]
        return sid, expiry

    async def personality(self, operation, payload, *, before_dispatch=None):
        if before_dispatch:
            await before_dispatch()
        if self.read_hook:
            self.read_hook()
        sid = payload["session_id"]
        human = self.bound[sid]
        self.reads.append((operation, human))
        if operation == "save":
            if human == "human1":
                raise AgentError("personality_forbidden")
            if self.conflict:
                raise AgentError("revision_conflict")
            self.saved.append(dict(payload))
            return {"revision": 2, "duplicate": False}
        value = {
            "revision": 1,
            "display_name": "Helper",
            "sections": [
                {
                    "title": "Identity",
                    "content": "<script>private " + human + "</script>",
                }
            ],
            "can_edit": human != "human1",
            "scope": "shared_agent",
            "updated_at": "2026-10-04T00:00:00+00:00",
        }
        return (
            {"versions": [value], "beforeRevision": None}
            if operation == "history"
            else value
        )


def start(client, csrf, model="local", consent=False):
    response = client.post(
        "/api/assistant/start",
        json={"modelId": model, "remoteConsent": consent},
        headers={"X-CSRF-TOKEN": csrf},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_model_selection_personality_routes_two_accounts_csrf_and_conflict(
    clients, monkeypatch
):
    a, b, generation, factory = clients
    ca = register(a, "a@fixture.test")
    cb = register(b, "b@fixture.test")
    configure(monkeypatch, factory)
    monkeypatch.setenv("STUDIO_AGENT_SETTINGS_ENABLED", "1")
    agent = SettingsAgent()
    install(a, agent)
    assert a.post("/api/assistant/models").status_code == 403
    ha = {"X-CSRF-TOKEN": ca}
    hb = {"X-CSRF-TOKEN": cb}
    assert (
        a.post("/api/assistant/start", json={"modelId": "api"}, headers=ha).status_code
        == 422
    )
    assert (
        a.post(
            "/api/assistant/start", json={"modelId": "not-granted"}, headers=ha
        ).status_code
        == 403
    )
    sa = start(a, ca, "api", True)
    sb = start(b, cb)
    assert sa["sessionKey"] != sb["sessionKey"]
    state = probe(a, ca)
    assert state["modelId"] == "api" and state["remoteConsent"]
    pa = a.post("/api/assistant/personality", json={}, headers=ha)
    pb = b.post("/api/assistant/personality", json={}, headers=hb)
    assert pa.status_code == pb.status_code == 200
    assert "human0" in pa.text and "human0" not in pb.text
    assert pa.headers["Cache-Control"] == "private, no-store"
    payload = {
        "sessionKey": sa["sessionKey"],
        "requestId": str(uuid.uuid4()),
        "expectedRevision": 1,
        "displayName": "Edited",
        "sections": [{"title": "Identity", "content": "new"}],
    }
    assert (
        b.post("/api/assistant/personality/save", json=payload, headers=hb).status_code
        == 409
    )
    own = {**payload, "sessionKey": sb["sessionKey"]}
    assert (
        b.post("/api/assistant/personality/save", json=own, headers=hb).status_code
        == 403
    )
    agent.conflict = True
    assert (
        a.post("/api/assistant/personality/save", json=payload, headers=ha).status_code
        == 409
    )
    agent.conflict = False
    assert (
        a.post("/api/assistant/personality/save", json=payload, headers=ha).json()[
            "revision"
        ]
        == 2
    )
    assert agent.saved[0]["session_id"] != payload["sessionKey"]
    assert "human" not in agent.saved[0] and generation.submit_count == 0
    # Explicit model switch invalidates the old Studio conversation/session handle.
    start(a, ca, "local")
    assert (
        a.post("/api/assistant/ask", json=advice(state), headers=ha).status_code == 409
    )


def test_inflight_logout_withholds_personality_and_history(clients, monkeypatch):
    a, _, _, factory = clients
    csrf = register(a, "only@fixture.test")
    configure(monkeypatch, factory)
    monkeypatch.setenv("STUDIO_AGENT_SETTINGS_ENABLED", "1")
    agent = SettingsAgent()
    install(a, agent)
    start(a, csrf)

    def revoke():
        with factory() as db:
            db.execute(delete(LoginSession))
            db.commit()

    agent.read_hook = revoke
    response = a.post(
        "/api/assistant/personality", json={}, headers={"X-CSRF-TOKEN": csrf}
    )
    assert response.status_code == 401 and "private" not in response.text


def test_invalid_personality_arguments_and_offline_setting_do_not_dispatch(
    clients, monkeypatch
):
    a, _, _, factory = clients
    csrf = register(a, "only@fixture.test")
    configure(monkeypatch, factory)
    agent = SettingsAgent()
    install(a, agent)
    assert (
        a.post(
            "/api/assistant/models", json={}, headers={"X-CSRF-TOKEN": csrf}
        ).status_code
        == 503
    )
    monkeypatch.setenv("STUDIO_AGENT_SETTINGS_ENABLED", "1")
    start(a, csrf)
    assert (
        a.post(
            "/api/assistant/personality",
            json={"human": "other"},
            headers={"X-CSRF-TOKEN": csrf},
        ).status_code
        == 422
    )
    assert not agent.reads
