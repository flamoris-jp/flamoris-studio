"""Availability permits bounded clock skew without extending freshness."""

from datetime import UTC, datetime, timedelta

import pytest

from flamoris_studio import agent_gateway
from flamoris_studio.agent_gateway import AgentError, AgentGateway


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "skew,lifetime,accepted",
    [
        (0.0283, 5, True),
        (0.999, 5, True),
        (1, 5, True),
        (1.001, 5, False),
        (-4.99, 5, True),
        (-5, 5, False),
        (-5.01, 5, False),
        (0, 0, False),
        (0, -1, False),
        (0, 5.001, False),
    ],
)
async def test_availability_clock_skew_is_bounded_and_expiry_is_not_extended(
    monkeypatch, skew, lifetime, accepted
):
    local_now = datetime(2026, 1, 1, tzinfo=UTC)

    class LocalClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return local_now

    monkeypatch.setattr(agent_gateway, "datetime", LocalClock)
    observed = local_now + timedelta(seconds=skew)
    remote_expiry = observed + timedelta(seconds=lifetime)
    calls = []

    async def call(name, payload):
        calls.append((name, payload))
        return {
            "ok": True,
            "available": True,
            "state": "ready",
            "principal_revision": 1,
            "context_revisions": [1],
            "proposal_revisions": [],
            "observed_at": observed.isoformat(),
            "expires_at": remote_expiry.isoformat(),
        }

    gateway = AgentGateway("http://127.0.0.1:8768", "x" * 40)
    gateway.call = call
    if accepted:
        result = await gateway.availability("synthetic-session")
        assert result["available"] is True and result["state"] == "ready"
        local_expiry = datetime.fromisoformat(result["expiresAt"])
        assert local_now < local_expiry <= remote_expiry
        assert local_expiry <= local_now + timedelta(seconds=lifetime)
    else:
        with pytest.raises(AgentError):
            await gateway.availability("synthetic-session")
    assert calls == [("ask_availability", {"session_id": "synthetic-session"})]
