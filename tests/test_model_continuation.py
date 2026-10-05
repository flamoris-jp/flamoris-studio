import uuid
from datetime import timedelta

from sqlalchemy import select
from test_assistant import advice, configure, install, probe
from test_assistant_settings import SettingsAgent, start
from test_studio import clients, register

from flamoris_studio.agent_gateway import AgentError
from flamoris_studio.db import AssistantModelSwitch, now

__all__ = ["clients"]


class SwitchingAgent(SettingsAgent):
    def __init__(self):
        super().__init__()
        self.switches = []
        self.transitions = {}
        self.lose_ack = False

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
            "expectedModelId": "local", "modelId": "api", "remoteConsent": True}


def test_switch_preserves_owned_handle_and_requires_complete_context_consent(clients, monkeypatch):
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
    assert a.post(path, json={**payload, "remoteConsent": False}, headers={"X-CSRF-TOKEN": ca}).status_code == 422
    assert not agent.switches
    changed = a.post(path, json=payload, headers={"X-CSRF-TOKEN": ca})
    assert changed.status_code == 200, changed.text
    assert changed.json()["sessionKey"] == state["sessionKey"]
    assert changed.json()["modelId"] == "api"
    repeated = a.post(path, json=payload, headers={"X-CSRF-TOKEN": ca})
    assert repeated.json() == changed.json() and len(agent.switches) == 1
    probe(a, ca)
    next_turn = a.post("/api/assistant/ask", json=advice(state, previousHandle=first.json()["requestHandle"]), headers={"X-CSRF-TOKEN": ca})
    assert next_turn.status_code == 200, next_turn.text
    assert "previous_conversation_id" in agent.asks[-1]
    assert agent.asks[-1]["session_id"] != agent.asks[0]["session_id"]
    assert len(agent.opens) == 1 and generation.submit_count == 0


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
