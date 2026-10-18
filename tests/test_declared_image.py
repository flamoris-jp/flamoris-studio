import uuid
from copy import deepcopy

import pytest
from sqlalchemy import select
from test_managed_inputs import clients, prepare_gateway, source
from test_studio import register

from flamoris_studio.db import Execution, ExecutionInput
from flamoris_studio.workflow_contract import normalize_catalog, map_parameters

__all__ = ["clients"]


def descriptor():
    return {
        "id": "comfy-" + "c" * 64,
        "name": "Any image model",
        "kind": "registered-comfy",
        "metadata_schema_version": 3,
        "definition_version": 1,
        "definition_digest": "c" * 64,
        "image": {
            "profile": "declared-comfy-v1",
            "mode": "declared",
            "dimensions": {"mode": "graph"},
        },
        "readiness": {
            "state": "validated",
            "basis": "installed_object_info",
            "live_provider_verified": False,
        },
        "parameters": {
            "source": {
                "type": "string",
                "role": "managed_input",
                "label": "Source image",
                "required": True,
            },
            "instruction": {
                "type": "string",
                "role": "prompt",
                "label": "Edit instruction",
                "required": True,
                "default": "Smile",
                "min_length": 1,
            },
            "quality": {
                "type": "integer",
                "role": "parameter",
                "label": "Quality",
                "required": False,
                "default": 2,
                "minimum": 1,
                "maximum": 5,
            },
        },
    }


def test_declared_mapping_and_opaque_handles():
    item = normalize_catalog({"descriptors": [descriptor()]})[0]
    handle = str(uuid.uuid4())
    assert map_parameters(
        item,
        {
            "imageInputs": {"source": handle},
            "additionalParameters": {"instruction": "Wave", "quality": 4},
        },
    ) == {"source": handle, "instruction": "Wave", "quality": 4}
    for body in (
        {"imageInputs": {"source": "a" * 32}},
        {
            "imageInputs": {"source": handle},
            "additionalParameters": {"source": "a" * 32},
        },
        {"imageInputs": {"source": handle}, "additionalParameters": {"quality": True}},
    ):
        with pytest.raises(ValueError):
            map_parameters(item, body)
    bad = deepcopy(descriptor())
    bad["parameters"]["source"]["default"] = "a" * 32
    assert normalize_catalog({"descriptors": [bad]}) == []


def prepare(a, csrf, gateway):
    asset = source(a, csrf)
    prepare_gateway(gateway)
    handle = a.post(
        "/api/generation/inputs",
        json={"assetId": asset["id"]},
        headers={"X-CSRF-TOKEN": csrf},
    ).json()["id"]
    item = normalize_catalog({"descriptors": [descriptor()]})[0]
    calls = []

    async def discover():
        calls.append("discover")
        return {"available": True, "managedInputReady": True, "workflows": [item]}

    async def build(item, params):
        calls.append(("build", params))
        return "private-recipe"

    gateway.discover, gateway.build_selected = discover, build
    return {
        "workflowId": item["id"],
        "workflowKind": item["kind"],
        "definitionVersion": 1,
        "definitionDigest": item["definitionDigest"],
        "imageInputs": {"source": handle},
        "additionalParameters": {"instruction": "Wave", "quality": 4},
    }, calls


def test_declared_images_require_ownership_before_discovery(clients):
    a, b, gateway, _ = clients
    ca, cb = (
        register(a, "declared-a@example.test"),
        register(b, "declared-b@example.test"),
    )
    body, calls = prepare(a, ca, gateway)
    before = list(gateway.input_calls)
    assert (
        b.post(
            "/api/generation/image/jobs", json=body, headers={"X-CSRF-TOKEN": cb}
        ).status_code
        == 404
    )
    assert not calls and before == gateway.input_calls


def test_declared_submit_result_download_and_settings_reuse(clients):
    a, _, gateway, factory = clients
    csrf = register(a, "declared-owner@example.test")
    body, calls = prepare(a, csrf, gateway)
    response = a.post(
        "/api/generation/image/jobs", json=body, headers={"X-CSRF-TOKEN": csrf}
    )
    assert response.status_code == 201, response.text
    built = next(call[1] for call in calls if isinstance(call, tuple))
    assert (
        len(built["source"]) == 32 and built["source"] != body["imageInputs"]["source"]
    )
    with factory() as db:
        execution = db.scalar(
            select(Execution).where(Execution.workflow == body["workflowId"])
        )
        assert execution.request_snapshot["imageInputs"] == body["imageInputs"]
        assert (
            db.scalar(
                select(ExecutionInput).where(
                    ExecutionInput.execution_id == execution.id
                )
            ).role
            == "source"
        )
    result = a.get("/api/executions/" + response.json()["id"] + "/result")
    assert result.status_code == 200, result.text
    assets = result.json()["assets"]
    assert assets
    detail = a.get("/api/assets/" + assets[0]["id"])
    assert detail.status_code == 200, detail.text
    assert detail.json()["settings"]["imageInputs"] == body["imageInputs"]
    assert (
        detail.json()["settings"]["additionalParameters"]
        == body["additionalParameters"]
    )
    assert a.get(assets[0]["downloadUrl"]).status_code == 200
