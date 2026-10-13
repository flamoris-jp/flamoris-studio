import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from test_assistant import advice, configure, install, probe
from test_assistant_settings import SettingsAgent, start
from test_studio import clients, register

from flamoris_studio.agent_gateway import AgentError
from flamoris_studio.db import AssistantModelSwitch, AssistantSession, now

__all__ = ["clients"]


class SwitchingAgent(SettingsAgent):
    def __init__(self):
        super().__init__()
        self.switches = []
        self.transitions = {}
        self.lose_ack = False

    async def models(self, keys):
        catalog = await super().models(keys)
        catalog["models"].append({"id": "api2", "display_name": "Other API",
                                  "data_flow": "remote_authorized"})
        return catalog

    async def continue_session(self, payload, *, before_dispatch=None):
        if before_dispatch:
            await before_dispatch()
        self.switches.append(dict(payload))
        key = (payload["session_id"], payload["request_id"])
        if key not in self.transitions:
            self.transitions[key] = (str(uuid.uuid4()), now() + timedelta(minutes=15))
        target, expiry = self.transitions[key]
        self.bound[target] = self.bound[payload["session_id"]]
        if self.lose_ack:
            raise AgentError("uncertain")
        return target, expiry


def switch_payload(state):
    return {"sessionKey": state["sessionKey"], "requestId": str(uuid.uuid4()),
            "expectedModelId": "local", "modelId": "api"}


def test_switch_preserves_owned_handle_without_browser_consent(clients, monkeypatch):
    a, b, generation, factory = clients
    ca, cb = register(a, "a@switch.test"), register(b, "b@switch.test")
    configure(monkeypatch, factory)
    monkeypatch.setenv("STUDIO_AGENT_SETTINGS_ENABLED", "1")
    agent = SwitchingAgent()
    install(a, agent)
    state = start(a, ca)
    first = a.post("/api/assistant/ask", json=advice(state), headers={"X-CSRF-TOKEN": ca})
    assert first.status_code == 200, first.text
    payload = switch_payload(state)
    path = "/api/assistant/switch"
    assert a.post(path, json=payload).status_code == 403
    assert b.post(path, json=payload, headers={"X-CSRF-TOKEN": cb}).status_code == 409
    assert not agent.switches
    changed = a.post(path, json=payload, headers={"X-CSRF-TOKEN": ca})
    assert changed.status_code == 200, changed.text
    assert changed.json()["sessionKey"] == state["sessionKey"]
    assert changed.json()["modelId"] == "api"
    assert agent.switches[0]["remote_consent"] is True
    repeated = a.post(path, json={**payload, "remoteConsent": False}, headers={"X-CSRF-TOKEN": ca})
    assert repeated.json() == changed.json() and len(agent.switches) == 1
    probe(a, ca)
    next_turn = a.post("/api/assistant/ask", json=advice(state, previousHandle=first.json()["requestHandle"]), headers={"X-CSRF-TOKEN": ca})
    assert next_turn.status_code == 200, next_turn.text
    assert "previous_conversation_id" in agent.asks[-1]
    assert agent.asks[-1]["session_id"] != agent.asks[0]["session_id"]
    assert len(agent.opens) == 1 and generation.submit_count == 0


@pytest.mark.parametrize("source,target", [("local", "api"), ("api", "api2"), ("api", "local")])
@pytest.mark.parametrize("legacy", [None, False, True])
def test_switch_policy_uses_allowed_model_not_legacy_browser_flag(clients, monkeypatch, source, target, legacy):
    a, _, _, factory = clients
    ca = register(a, "policy@switch.test")
    configure(monkeypatch, factory)
    monkeypatch.setenv("STUDIO_AGENT_SETTINGS_ENABLED", "1")
    agent = SwitchingAgent()
    install(a, agent)
    state = start(a, ca, source)
    payload = {**switch_payload(state), "expectedModelId": source, "modelId": target}
    if legacy is not None:
        payload["remoteConsent"] = legacy
    response = a.post("/api/assistant/switch", json=payload, headers={"X-CSRF-TOKEN": ca})
    assert response.status_code == 200, response.text
    assert response.json()["sessionKey"] == state["sessionKey"]
    assert agent.switches[0]["remote_consent"] is (target != "local")
    assert a.post("/api/assistant/ask", json=advice(state), headers={"X-CSRF-TOKEN": ca}).status_code == 200


def test_legacy_pending_local_handoff_keeps_original_wire_payload(clients, monkeypatch):
    a, _, _, factory = clients
    ca = register(a, "legacy@switch.test")
    configure(monkeypatch, factory)
    monkeypatch.setenv("STUDIO_AGENT_SETTINGS_ENABLED", "1")
    agent = SwitchingAgent()
    install(a, agent)
    state = start(a, ca, "api")
    payload = {**switch_payload(state), "expectedModelId": "api", "modelId": "local"}
    with factory() as db:
        binding = db.scalar(select(AssistantSession))
        source = binding.upstream_session_id
        db.add(AssistantModelSwitch(user_id=binding.user_id,
            request_id=uuid.UUID(payload["requestId"]), session_id=binding.id,
            source_session_id=source, model_id="local", remote_consent=True,
            binding_digest=binding.binding_digest, state="uncertain"))
        db.commit()
    expected = {"session_id": str(source), "request_id": payload["requestId"],
                "model_id": "local", "remote_consent": True}
    response = a.post("/api/assistant/switch", json=payload, headers={"X-CSRF-TOKEN": ca})
    assert response.status_code == 200, response.text
    assert agent.switches == [expected]
    repeated = a.post("/api/assistant/switch", json={**payload, "remoteConsent": False}, headers={"X-CSRF-TOKEN": ca})
    assert repeated.json() == response.json() and agent.switches == [expected]


def test_ungranted_model_cannot_switch_even_with_legacy_true(clients, monkeypatch):
    a, _, _, factory = clients
    ca = register(a, "denied@switch.test")
    configure(monkeypatch, factory)
    monkeypatch.setenv("STUDIO_AGENT_SETTINGS_ENABLED", "1")
    agent = SwitchingAgent()
    install(a, agent)
    state = start(a, ca)
    response = a.post("/api/assistant/switch", json={**switch_payload(state),
        "modelId": "not-granted", "remoteConsent": True}, headers={"X-CSRF-TOKEN": ca})
    assert response.status_code == 403 and not agent.switches


def test_lost_handoff_ack_is_fenced_and_explicit_retry_reuses_identity(clients, monkeypatch):
    a, _, _, factory = clients
    ca = register(a, "unknown@switch.test")
    configure(monkeypatch, factory)
    monkeypatch.setenv("STUDIO_AGENT_SETTINGS_ENABLED", "1")
    agent = SwitchingAgent()
    install(a, agent)
    state = start(a, ca)
    payload = switch_payload(state)
    agent.lose_ack = True
    assert a.post("/api/assistant/switch", json=payload, headers={"X-CSRF-TOKEN": ca}).status_code == 503
    assert a.post("/api/assistant/ask", json=advice(state), headers={"X-CSRF-TOKEN": ca}).status_code == 409
    with factory() as db:
        assert db.scalar(select(AssistantModelSwitch)).state == "uncertain"
    assert not agent.asks and len(agent.switches) == 1
    agent.lose_ack = False
    resumed = a.post("/api/assistant/switch", json=payload, headers={"X-CSRF-TOKEN": ca})
    assert resumed.status_code == 200, resumed.text
    assert agent.switches[0] == agent.switches[1]
    with factory() as db:
        assert db.scalar(select(AssistantModelSwitch)).state == "completed"


def test_unknown_question_blocks_handoff_before_dispatch(clients, monkeypatch):
    a, _, _, factory = clients
    ca = register(a, "pending@switch.test")
    configure(monkeypatch, factory)
    monkeypatch.setenv("STUDIO_AGENT_SETTINGS_ENABLED", "1")
    agent = SwitchingAgent()
    install(a, agent)
    state = start(a, ca)
    agent.fail = True
    assert a.post("/api/assistant/ask", json=advice(state), headers={"X-CSRF-TOKEN": ca}).status_code == 503
    assert a.post("/api/assistant/switch", json=switch_payload(state), headers={"X-CSRF-TOKEN": ca}).status_code == 409
    assert not agent.switches
