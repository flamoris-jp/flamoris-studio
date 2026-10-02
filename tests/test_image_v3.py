import copy
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from test_studio import clients as original_clients
from test_studio import image_request, register

from flamoris_studio.db import Execution
from flamoris_studio.gateway import GatewayError, GenerationGateway
from flamoris_studio.image_v3_contract import normalize_v3_catalog
from flamoris_studio.workflow_contract import map_parameters

clients = original_clients

BASE = json.loads(
    (Path(__file__).parent / "fixtures/image-v3-descriptor.json").read_text()
)
PARAMETERS = {"checkpoint": "test", "positive_prompt": "x"}
DIGEST = "sha256:" + "a" * 64


def catalog(raw=None):
    return {
        "descriptor_revision": 3,
        "descriptors": [copy.deepcopy(BASE) if raw is None else raw],
    }


def test_reviewed_parent_maps_roles_and_namespaces_identity_without_graph():
    item = normalize_v3_catalog(catalog())[0]
    assert (
        item["kind"] == "v3" and item["id"] == "v3:image-parent" and item["selectable"]
    )
    assert item["definitionDigest"] == BASE["digest"]
    assert item["parameters"]["checkpoint"]["enum"] == ["test"]
    assert "artifact" not in json.dumps(item) and "graph" not in json.dumps(item)
    values = map_parameters(
        item,
        {
            "checkpoint": "test",
            "positivePrompt": "x",
            "width": 512,
            "height": 512,
            "seed": 4,
        },
    )
    assert values["positive_prompt"] == "x" and values["steps"] == 20
    assert "sampler" not in values and "scheduler" not in values
    with pytest.raises(ValueError):
        map_parameters(
            item,
            {
                "checkpoint": "other",
                "positivePrompt": "x",
                "width": 512,
                "height": 512,
                "seed": 4,
            },
        )


@pytest.mark.parametrize(
    "change", ["profile", "role", "output", "multiple", "bounds", "boolean_revision"]
)
def test_unknown_or_malformed_v3_contracts_fail_closed(change):
    raw = copy.deepcopy(BASE)
    if change == "profile":
        raw["profile"]["id"] = "music-generate-v1"
    elif change == "role":
        raw["inputs"]["width"]["role"] = "arbitrary"
    elif change == "output":
        raw["outputs"]["image"]["role"] = "preview"
    elif change == "multiple":
        raw["outputs"]["image"]["max_count"] = 2
    elif change == "bounds":
        raw["inputs"]["steps"]["maximum"] = 999999
    else:
        raw["profile"]["revision"] = True
    assert normalize_v3_catalog(catalog(raw)) == []


@pytest.mark.parametrize("change", ["validated", "domain", "model", "fixed", "revoked"])
def test_unqualified_or_unsupported_roots_are_never_selectable(change):
    raw = copy.deepcopy(BASE)
    if change == "domain":
        raw["qualified_domain"]["revision"] = 2
    elif change == "model":
        raw["qualified_models"] = []
    elif change == "fixed":
        raw["inputs"].pop("width")
        raw["inputs"].pop("height")
    else:
        raw["readiness"]["state"] = change
    assert not normalize_v3_catalog(catalog(raw))[0]["selectable"]


def test_utf8_json_byte_bound_is_preserved_and_duplicate_catalog_is_rejected():
    raw = copy.deepcopy(BASE)
    raw["inputs"]["positive_prompt"]["max_bytes"] = 10
    item = normalize_v3_catalog(catalog(raw))[0]
    with pytest.raises(ValueError):
        map_parameters(
            item,
            {
                "checkpoint": "test",
                "positivePrompt": "猫猫猫猫",
                "width": 512,
                "height": 512,
                "seed": 4,
            },
        )
    with pytest.raises(ValueError):
        normalize_v3_catalog({"descriptor_revision": 3, "descriptors": [BASE, BASE]})


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_gateway_discovery_is_explicit_and_preserves_legacy(monkeypatch, enabled):
    gateway = GenerationGateway()
    monkeypatch.setenv("STUDIO_GENERATION_V3_ENABLED", str(enabled).lower())
    calls = []

    async def response(name, args=None):
        calls.append(name)
        return {
            "system.health": {"healthy": True},
            "capabilities.list": {
                "capabilities": [{"id": "image.generate", "available": True}]
            },
            "models.list": {"models": []},
            "workflows.list": {"descriptors": []},
            "workflows.v3.list": catalog(),
        }[name]

    monkeypatch.setattr(gateway, "_json", response)
    result = await gateway.discover()
    assert bool(result["workflows"]) is enabled
    assert ("workflows.v3.list" in calls) is enabled


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "version",
        "digest",
        "schema",
        "compiler",
        "invocation",
        "not_ready",
        "boolean",
    ],
)
async def test_gateway_build_pins_ready_root_and_validates_reply(monkeypatch, invalid):
    monkeypatch.setenv("STUDIO_GENERATION_V3_ENABLED", "true")
    item = normalize_v3_catalog(catalog())[0]
    gateway = GenerationGateway()
    calls = []

    async def response(name, args=None):
        calls.append((name, args))
        assert name == "workflows.v3.build"
        assert args == {
            "workflow_id": "image-parent",
            "definition_version": 1,
            "definition_digest": BASE["digest"],
            "require_ready": True,
            "parameters": PARAMETERS,
        }
        result = {
            "workflow_id": "a" * 32,
            "template": "image-parent",
            "schema_version": 3,
            "definition_version": 1,
            "definition_digest": BASE["digest"],
            "require_ready": True,
            "parameters": PARAMETERS,
            "structural_digest": DIGEST,
            "closure_digest": DIGEST,
            "invocation_digest": DIGEST,
            "compiler_revision": 2,
            "adapter_revision": 1,
        }
        field = {
            "version": "definition_version",
            "digest": "definition_digest",
            "schema": "schema_version",
            "compiler": "compiler_revision",
            "invocation": "invocation_digest",
            "not_ready": "require_ready",
        }.get(invalid)
        if field:
            result[field] = False
        if invalid == "boolean":
            result["adapter_revision"] = True
        return result

    monkeypatch.setattr(gateway, "_json", response)
    if invalid:
        with pytest.raises(GatewayError):
            await gateway.build_selected(item, PARAMETERS)
    else:
        assert await gateway.build_selected(item, PARAMETERS) == "a" * 32
    assert len(calls) == 1


def test_owner_csrf_stale_ready_and_snapshot_boundaries(clients):
    a, b, gateway, factory = clients
    ca, cb = register(a, "v3-owner@example.test"), register(b, "v3-other@example.test")
    selected = normalize_v3_catalog(catalog())[0]
    calls = []

    async def discovery():
        return {"available": True, "workflows": [selected]}

    async def build(item, parameters):
        calls.append((item["kind"], parameters))
        return "opaque-workflow"

    gateway.discover, gateway.build_selected = discovery, build
    body = {
        **image_request(),
        "workflowId": selected["id"],
        "workflowKind": "v3",
        "definitionVersion": 1,
        "definitionDigest": selected["definitionDigest"],
    }
    path = "/api/generation/image/jobs"
    assert a.post(path, json=body).status_code == 403
    assert (
        a.post(
            path, json=body | {"definitionVersion": 2}, headers={"X-CSRF-TOKEN": ca}
        ).status_code
        == 409
    )
    assert (
        a.post(
            path, json=body | {"require_ready": False}, headers={"X-CSRF-TOKEN": ca}
        ).status_code
        == 422
    )
    assert not calls and gateway.submit_count == 0
    result = a.post(path, json=body, headers={"X-CSRF-TOKEN": ca})
    assert result.status_code == 201, result.text
    execution_id = result.json()["id"]
    assert calls[0][0] == "v3" and gateway.submit_count == 1
    assert b.get("/api/executions/" + execution_id).status_code == 404
    assert b.get("/api/executions/" + execution_id + "/result").status_code == 404
    assert (
        b.post(
            "/api/executions/" + execution_id + "/cancel",
            headers={"X-CSRF-TOKEN": cb},
        ).status_code
        == 404
    )
    with factory() as db:
        row = db.get(Execution, uuid.UUID(execution_id))
        assert row.request_snapshot["workflowKind"] == "v3"
        assert row.request_snapshot["definitionDigest"] == BASE["digest"]
        assert row.request_snapshot["generationContract"]["compilerRevision"] == 2
        assert "sampler" not in row.request_snapshot["normalizedParameters"]
        assert len(db.scalars(select(Execution)).all()) == 1
    selected["selectable"] = False
    assert a.post(path, json=body, headers={"X-CSRF-TOKEN": ca}).status_code == 409
    assert gateway.submit_count == 1


@pytest.mark.asyncio
async def test_enabled_catalog_failure_never_falls_back_and_disabled_build_never_calls(
    monkeypatch,
):
    monkeypatch.setenv("STUDIO_GENERATION_V3_ENABLED", "true")
    gateway = GenerationGateway()
    calls = []

    async def response(name, args=None):
        calls.append(name)
        if name == "workflows.v3.list":
            raise GatewayError("unavailable")
        return {
            "system.health": {"healthy": True},
            "capabilities.list": {
                "capabilities": [{"id": "image.generate", "available": True}]
            },
            "models.list": {"models": []},
            "workflows.list": {"descriptors": []},
        }[name]

    monkeypatch.setattr(gateway, "_json", response)
    with pytest.raises(GatewayError):
        await gateway.discover()
    assert calls[-1] == "workflows.v3.list" and calls.count("workflows.list") == 1
    monkeypatch.setenv("STUDIO_GENERATION_V3_ENABLED", "false")
    before = list(calls)
    with pytest.raises(GatewayError):
        await gateway.build_selected(normalize_v3_catalog(catalog())[0], PARAMETERS)
    assert calls == before


def test_v3_build_failure_stops_before_job_submission_and_keeps_snapshot(clients):
    a, _, gateway, factory = clients
    ca = register(a, "v3-failure@example.test")
    selected = normalize_v3_catalog(catalog())[0]
    calls = []

    async def discovery():
        return {"available": True, "workflows": [selected]}

    async def reject(item, parameters):
        calls.append("build")
        raise GatewayError("validation")

    gateway.discover, gateway.build_selected = discovery, reject
    body = {
        **image_request(),
        "workflowId": selected["id"],
        "workflowKind": "v3",
        "definitionVersion": 1,
        "definitionDigest": selected["definitionDigest"],
    }
    result = a.post(
        "/api/generation/image/jobs", json=body, headers={"X-CSRF-TOKEN": ca}
    )
    assert result.status_code >= 400
    assert calls == ["build"] and gateway.submit_count == 0
    with factory() as db:
        execution = db.scalar(select(Execution))
        assert execution.last_known_status == "failed"
        assert execution.request_snapshot["definitionDigest"] == BASE["digest"]
        assert execution.request_snapshot["positivePrompt"] == body["positivePrompt"]


@pytest.mark.parametrize("code", ["busy", "unavailable"])
def test_v3_uncertain_submission_freezes_the_original_owner_request_without_replay(
    clients, code
):
    a, b, gateway, factory = clients
    ca, _ = (
        register(a, "v3-uncertain@example.test"),
        register(b, "v3-unrelated@example.test"),
    )
    selected = normalize_v3_catalog(catalog())[0]
    calls = []

    async def discovery():
        return {"available": True, "workflows": [selected]}

    async def build(item, parameters):
        return "opaque-workflow"

    async def submit(workflow):
        calls.append(workflow)
        raise GatewayError(code)

    gateway.discover, gateway.build_selected, gateway.submit = discovery, build, submit
    body = {
        **image_request(),
        "workflowId": selected["id"],
        "workflowKind": "v3",
        "definitionVersion": 1,
        "definitionDigest": selected["definitionDigest"],
    }
    assert (
        a.post(
            "/api/generation/image/jobs", json=body, headers={"X-CSRF-TOKEN": ca}
        ).status_code
        >= 400
    )
    with factory() as db:
        execution = db.scalar(select(Execution))
        assert execution.last_known_status == (
            "busy" if code == "busy" else "submission_unknown"
        )
        snapshot = copy.deepcopy(execution.request_snapshot)
        execution_id = str(execution.id)
    assert a.get("/api/executions/" + execution_id).status_code == 200
    assert b.get("/api/executions/" + execution_id).status_code == 404
    with factory() as db:
        assert db.scalar(select(Execution)).request_snapshot == snapshot
    assert calls == ["opaque-workflow"]
