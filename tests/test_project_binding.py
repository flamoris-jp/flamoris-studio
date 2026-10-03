import json
import uuid

import pytest
from fastapi import HTTPException

from flamoris_studio.assistant import configured_actor


def configure(monkeypatch, **overrides):
    row = {
        "user_id": str(uuid.uuid4()),
        "human": "person",
        "agent": "helper",
        "project": "example.project",
    }
    row.update(overrides)
    monkeypatch.setenv("STUDIO_AGENT_BINDINGS", json.dumps([row]))
    monkeypatch.setenv("STUDIO_AGENT_ENDPOINT", "http://127.0.0.1:8768/mcp")
    monkeypatch.setenv("STUDIO_AGENT_TOKEN", "x" * 40)
    return row


@pytest.mark.parametrize(
    "project", ["example.project", "example.ai.helper", "a.b", "a" * 64]
)
def test_dotted_project_mapping_preserves_exact_principal(monkeypatch, project):
    row = configure(monkeypatch, project=project)
    actor = configured_actor(uuid.UUID(row["user_id"]))
    assert actor[0] == {k: row[k] for k in ("human", "agent", "project")}


@pytest.mark.parametrize(
    "project", ["", ".a", "a.", "a..b", "../a", "a/b", "a:b", "a b", "a" * 65, 1, None]
)
def test_malformed_project_mapping_is_unavailable(monkeypatch, project):
    row = configure(monkeypatch, project=project)
    with pytest.raises(HTTPException) as caught:
        configured_actor(uuid.UUID(row["user_id"]))
    assert caught.value.status_code == 503


@pytest.mark.parametrize("field", ["human", "agent"])
def test_dotted_project_support_keeps_other_principal_rules(monkeypatch, field):
    row = configure(monkeypatch, **{field: "dotted.name"})
    with pytest.raises(HTTPException):
        configured_actor(uuid.UUID(row["user_id"]))
