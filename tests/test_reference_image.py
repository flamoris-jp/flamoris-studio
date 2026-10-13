import uuid
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from test_managed_inputs import clients, prepare_gateway, source
from test_studio import image_request, register

from flamoris_generation_controller.models import ModelCatalog
from flamoris_generation_controller.config import Settings
from flamoris_generation_controller.reference_comfy import conditioning_prompt
from flamoris_generation_controller.workflows import (
    Conditioning,
    Parameters,
    WorkflowStore,
)
from flamoris_studio.app import WorkflowImageRequest
from flamoris_studio.db import Execution, ExecutionInput, ManagedInput, now
from flamoris_studio.gateway import GatewayError
from flamoris_studio.managed_inputs import protected, prune
from flamoris_studio.workflow_contract import (
    REFERENCE_FIELDS,
    map_parameters,
    normalize_catalog,
)

__all__ = ["clients"]


def descriptor(tmp_path):
    store = WorkflowStore(ModelCatalog(Settings()), tmp_path / "definitions")
    graph = conditioning_prompt(
        Parameters(checkpoint="test", positive_prompt="A new scene"),
        Conditioning(
            character_adapter="adapter.safetensors",
            style_adapter="adapter.safetensors",
            clip_vision="vision.safetensors",
            controlnet="control.safetensors",
        ),
        tuple(REFERENCE_FIELDS),
    )
    return normalize_catalog(
        {"descriptors": [store.definitions.register("References", graph)]}
    )[0]


def test_reference_metadata_accepts_only_current_declared_profile_and_opaque_handles(
    tmp_path,
):
    selected = descriptor(tmp_path)
    assert selected["selectable"] and selected["referenceRoles"] == list(
        REFERENCE_FIELDS
    )
    assert selected["liveProviderVerified"] is False and "graph" not in selected
    body = {
        **image_request(),
        **{field: str(uuid.uuid4()) for field in REFERENCE_FIELDS.values()},
    }
    params = map_parameters(selected, body)
    assert {params[role] for role in REFERENCE_FIELDS} == {
        body[field] for field in REFERENCE_FIELDS.values()
    }
    with pytest.raises(ValueError):
        map_parameters(
            selected, {**body, "additionalParameters": {"character_image": "a" * 32}}
        )
    with pytest.raises(ValueError):
        map_parameters(selected, {**body, "characterInputId": "a" * 32})
    with pytest.raises(ValueError):
        map_parameters(selected, {**body, "loras": [{"name": "layer"}]})


def test_builtin_dto_still_rejects_reference_and_definition_injection_before_io():
    for fields in (
        {"referenceInputId": str(uuid.uuid4())},
        {"characterInputId": str(uuid.uuid4())},
        {"definitionVersion": 1},
        {"definitionDigest": "a" * 64},
    ):
        with pytest.raises(ValidationError):
            WorkflowImageRequest(
                **image_request(),
                workflowId="text-to-image",
                workflowKind="builtin",
                **fields,
            )


def prepare(a, csrf, gateway, tmp_path):
    asset = source(a, csrf)
    prepare_gateway(gateway)
    handles = {
        role: a.post(
            "/api/generation/inputs",
            json={"assetId": asset["id"]},
            headers={"X-CSRF-TOKEN": csrf},
        ).json()["id"]
        for role in REFERENCE_FIELDS
    }
    selected = descriptor(tmp_path)
    calls = []

    async def discover():
        calls.append("discover")
        return {"available": True, "managedInputReady": True, "workflows": [selected]}

    async def build(item, params):
        calls.append(("build", params))
        return "private-built-recipe"

    gateway.discover, gateway.build_selected = discover, build
    body = {
        **image_request(),
        "workflowId": selected["id"],
        "workflowKind": "registered-comfy",
        "definitionVersion": 1,
        "definitionDigest": selected["definitionDigest"],
        **{REFERENCE_FIELDS[role]: handle for role, handle in handles.items()},
    }
    return handles, body, calls


def test_all_image_roles_are_owned_before_any_upstream_call(clients, tmp_path):
    a, b, gateway, factory = clients
    ca, cb = (
        register(a, "reference-a@example.test"),
        register(b, "reference-b@example.test"),
    )
    handles, body, calls = prepare(a, ca, gateway, tmp_path)
    before = list(gateway.input_calls)
    assert (
        b.post(
            "/api/generation/image/jobs", json=body, headers={"X-CSRF-TOKEN": cb}
        ).status_code
        == 404
    )
    assert not calls and gateway.input_calls == before
    with factory() as db:
        row = db.get(ManagedInput, uuid.UUID(handles["style_image"]))
        row.expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert (
        a.post(
            "/api/generation/image/jobs", json=body, headers={"X-CSRF-TOKEN": ca}
        ).status_code
        == 409
    )
    assert not calls and gateway.input_calls == before


@pytest.mark.parametrize("unknown", [False, True])
def test_durable_four_reference_fences_settings_restore_and_terminal_cleanup(
    clients, tmp_path, unknown
):
    a, _, gateway, factory = clients
    csrf = register(a, "reference-owner@example.test")
    handles, body, calls = prepare(a, csrf, gateway, tmp_path)
    body["additionalParameters"] = {"character_strength": 0.45, "style_strength": 0.3}
    if unknown:

        async def submit(_):
            raise GatewayError("unavailable")

        gateway.submit = submit
    response = a.post(
        "/api/generation/image/jobs", json=body, headers={"X-CSRF-TOKEN": csrf}
    )
    assert response.status_code == (503 if unknown else 201), response.text
    built = next(call[1] for call in calls if isinstance(call, tuple))
    assert all(
        built[role] != handles[role] and len(built[role]) == 32 for role in handles
    )
    assert built["character_strength"] == 0.45
    with factory() as db:
        execution = db.scalar(
            select(Execution).where(Execution.workflow == body["workflowId"])
        )
        assert execution.last_known_status == (
            "submission_unknown" if unknown else "queued"
        )
        assert (
            len(
                db.scalars(
                    select(ExecutionInput).where(
                        ExecutionInput.execution_id == execution.id
                    )
                ).all()
            )
            == 4
        )
        for role, handle in handles.items():
            assert execution.request_snapshot[REFERENCE_FIELDS[role]] == handle
            assert execution.request_snapshot["normalizedParameters"][role] == handle
            assert protected(db, uuid.UUID(handle))
    for handle in handles.values():
        assert (
            a.delete(
                "/api/generation/inputs/" + handle, headers={"X-CSRF-TOKEN": csrf}
            ).status_code
            == 409
        )
    if not unknown:
        asset = a.get("/api/executions/" + response.json()["id"] + "/result").json()[
            "assets"
        ][0]
        detail = a.get("/api/assets/" + asset["id"]).json()
        assert detail["settings"]["characterInputId"] == handles["character_image"]
        assert detail["settings"]["additionalParameters"]["style_strength"] == 0.3
        assert set(detail["referenceInputs"]) == set(handles)
        assert all(item["available"] for item in detail["referenceInputs"].values())
        assert all(len(item["id"]) == 36 for item in detail["referenceInputs"].values())
        for handle in handles.values():
            assert (
                a.delete(
                    "/api/generation/inputs/" + handle, headers={"X-CSRF-TOKEN": csrf}
                ).status_code
                == 200
            )
    else:
        with factory() as db:
            for handle in handles.values():
                row = db.get(ManagedInput, uuid.UUID(handle))
                row.terminal_at = row.expires_at = now() - timedelta(days=2)
            db.commit()
            assert prune(db, a.app.state.input_thumbnails) == 0
            assert all(
                db.get(ManagedInput, uuid.UUID(handle)) for handle in handles.values()
            )
