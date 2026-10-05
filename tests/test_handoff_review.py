from sqlalchemy import select
from test_assistant import configure, install
from test_assistant_settings import start
from test_model_continuation import SwitchingAgent, switch_payload
from test_studio import clients, register

from flamoris_studio.agent_gateway import AgentError
from flamoris_studio.db import AssistantModelSwitch

__all__ = ["clients"]


def test_rejected_handoff_retry_is_fenced_before_redispatch(clients, monkeypatch):
    client, _, _, factory = clients
    csrf = register(client, "rejected-retry@switch.test")
    configure(monkeypatch, factory)
    monkeypatch.setenv("STUDIO_AGENT_SETTINGS_ENABLED", "1")
    agent = SwitchingAgent()
    install(client, agent)
    state = start(client, csrf)
    payload = switch_payload(state)
    original = agent.continue_session

    async def busy(*args, **kwargs):
        raise AgentError("busy")

    agent.continue_session = busy
    headers = {"X-CSRF-TOKEN": csrf}
    assert client.post("/api/assistant/switch", json=payload, headers=headers).status_code == 503
    with factory() as db:
        assert db.scalar(select(AssistantModelSwitch)).state == "rejected"

    async def lost_ack(*args, **kwargs):
        with factory() as db:
            assert db.scalar(select(AssistantModelSwitch)).state == "uncertain"
        agent.lose_ack = True
        return await original(*args, **kwargs)

    agent.continue_session = lost_ack
    assert client.post("/api/assistant/switch", json=payload, headers=headers).status_code == 503
    with factory() as db:
        assert db.scalar(select(AssistantModelSwitch)).state == "uncertain"
