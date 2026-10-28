"""Studio-owned video orchestration; fixtures are synthetic, never GPU evidence."""
import copy
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from test_managed_inputs import prepare_gateway, source
from test_multimedia_results import BytesGateway
from test_studio import clients, register

from flamoris_studio.app import create_app
from flamoris_studio.db import Execution, ExecutionInput, LoginSession, ManagedInput, now
from flamoris_studio.gateway import GatewayError, GenerationGateway
from flamoris_studio.video_contract import normalize_video_catalog, normalize_video_descriptor, video_parameters
from flamoris_studio.workflow_contract import normalize_catalog

__all__ = ["clients"]
JOB, RECIPE = "b" * 32, "a" * 32


def descriptor(mode="i2v"):
    parameters = {}
    for name in ("start_image", "end_image") if mode == "flf2v" else ("start_image",):
        parameters[name] = {"type": "string", "role": "managed_input", "label": name, "required": True}
    for name, default, low, high in (("width", 768, 64, 1024), ("height", 432, 64, 1024), ("frames", 81, 5, 161), ("fps", 16, 1, 32), ("seed", 0, 0, 2**53 - 1)):
        parameters[name] = {"type": "integer", "role": "parameter", "label": name, "required": False, "default": default, "minimum": low, "maximum": high}
    parameters["motion"] = {"type": "string", "role": "prompt", "label": "Motion", "required": False, "default": "A slow step", "min_length": 1, "max_length": 20000}
    parameters["model"] = {"type": "string", "role": "model", "label": "Model", "required": False, "default": "fixture.safetensors", "enum": ["fixture.safetensors"], "model_kind": "diffusion_model"}
    return {"id": "comfy-" + "d" * 64, "kind": "registered-comfy", "version": 1,
            "metadata_schema_version": 4, "definition_version": 1, "definition_digest": "d" * 64,
            "provider_id": "comfyui", "capability_id": "video.generate", "name": "Synthetic video",
            "video": {"profile": "wan-video-v1", "mode": mode, "conditioning_node": "3"},
            "readiness": {"state": "validated", "basis": "installed_object_info", "live_provider_verified": False, "generation_verified": False},
            "parameters": parameters}


@pytest.mark.parametrize("mode", ["i2v", "flf2v"])
def test_descriptor_roles_are_distinct_and_not_image_templates(mode):
    raw = descriptor(mode)
    item = normalize_video_descriptor(raw)
    handles = {key: str(uuid.uuid4()) for key in item["referenceRoles"]}
    values = video_parameters(item, {"imageInputs": handles, "additionalParameters": {"seed": 0}})
    assert values["seed"] == 0 and values["start_image"] == handles["start_image"]
    assert ("end_image" in values) == (mode == "flf2v")
    assert normalize_catalog({"descriptors": [raw]}) == []
    with pytest.raises(ValueError):
        normalize_video_catalog({"descriptors": [raw, raw]})


@pytest.mark.parametrize("bad", ["version", "metadata", "provider", "profile", "managed_default", "ignored_end", "width", "frames", "dynamic_parameter"])
def test_malformed_video_catalog_fails_closed(bad):
    raw = descriptor()
    if bad == "version": raw["definition_version"] = True
    elif bad == "metadata": raw["metadata_schema_version"] = 3
    elif bad == "provider": raw["provider_id"] = "other"
    elif bad == "profile": raw["video"]["profile"] = "other"
    elif bad == "managed_default": raw["parameters"]["start_image"]["default"] = "a" * 32
    elif bad == "ignored_end": raw["video"]["mode"] = "flf2v"
    elif bad == "width": raw["parameters"]["width"]["maximum"] = 4096
    elif bad == "frames": raw["parameters"]["frames"]["default"] = 80
    elif bad == "dynamic_parameter": raw["parameters"]["motion"]["type"] = "object"
    assert normalize_video_catalog({"descriptors": [raw]}) == []


@pytest.mark.parametrize("changes", [{"width": 769}, {"height": True}, {"frames": 80}, {"fps": 0}, {"fps": 8}, {"width": 1024, "height": 1024}, {"seed": 2**53}, {"start_image": "a" * 32}, {"unknown": 1}, {"motion": {"raw": "graph"}}])
def test_video_parameters_do_not_widen_budgets_or_input_authority(changes):
    item = normalize_video_descriptor(descriptor())
    with pytest.raises(ValueError):
        video_parameters(item, {"imageInputs": {"start_image": str(uuid.uuid4())}, "additionalParameters": changes})


@pytest.mark.asyncio
async def test_gateway_discovery_requires_video_capability_inputs_and_models(monkeypatch):
    gateway = GenerationGateway()
    state = {"ready": True, "cap": True, "models": True}
    async def response(name, args=None):
        return {"system.health": {"healthy": True, "managed_input_support": {"ready": state["ready"]}},
                "capabilities.list": {"capabilities": [{"id": "video.generate", "provider_id": "comfyui", "available": state["cap"]}]},
                "workflows.list": {"descriptors": [descriptor()]},
                "models.list": {"models": [{"kind": "diffusion_model", "name": "fixture.safetensors"}] if state["models"] else []}}[name]
    monkeypatch.setattr(gateway, "_json", response)
    assert (await gateway.discover_video())["available"]
    state["models"] = False
    result = await gateway.discover_video()
    assert not result["available"] and result["workflows"][0]["reason"] == "reference_models_unavailable"
    state.update(models=True, ready=False)
    assert (await gateway.discover_video())["workflows"][0]["reason"] == "reference_storage_unavailable"
    state["cap"] = False
    assert (await gateway.discover_video())["workflows"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, "schema", "template", "digest", "images", "parameters", "boolean_number"])
async def test_build_echo_is_exact_before_submit(monkeypatch, bad):
    item = normalize_video_descriptor(descriptor())
    parameters = video_parameters(item, {"imageInputs": {"start_image": str(uuid.uuid4())}, "additionalParameters": {"seed": 1}})
    parameters["start_image"] = "e" * 32
    reply = {"workflow_id": RECIPE, "template": "registered-video", "schema_version": 11, "definition_id": item["id"],
             "images": {"start_image": parameters["start_image"]}, "parameters": {key: value for key, value in parameters.items() if key != "start_image"}}
    if bad == "schema": reply["schema_version"] = 10
    elif bad == "template": reply["template"] = "registered-comfy"
    elif bad == "digest": reply["definition_id"] = "comfy-" + "f" * 64
    elif bad == "images": reply["images"]["end_image"] = "f" * 32
    elif bad == "parameters": reply["parameters"]["unreviewed"] = 1
    elif bad == "boolean_number": reply["parameters"]["seed"] = True
    gateway = GenerationGateway()
    async def response(name, args=None): return reply
    monkeypatch.setattr(gateway, "_json", response)
    if bad is None: assert await gateway.build_selected(item, parameters) == RECIPE
    else:
        with pytest.raises(GatewayError): await gateway.build_selected(item, parameters)


def setup(clients, mode="i2v"):
    a, b, gateway, factory = clients
    ca, cb = register(a, "video-a@example.test"), register(b, "video-b@example.test")
    asset = source(a, ca)
    prepare_gateway(gateway)
    handle = a.post("/api/generation/inputs", json={"assetId": asset["id"]}, headers={"X-CSRF-TOKEN": ca}).json()["id"]
    item = normalize_video_descriptor(descriptor(mode))
    calls, submissions = [], []
    async def discover(): calls.append("discover"); return {"available": True, "managedInputReady": True, "workflows": [item]}
    async def build(selected, parameters): calls.append(("build", copy.deepcopy(parameters))); return RECIPE
    async def submit(recipe): submissions.append(recipe); return {"job_id": JOB, "workflow_id": recipe, "operation": "video.generate", "status": "queued"}
    async def assets(job): return [{"asset_id": "video-upstream", "filename": "clip.mp4", "mime_type": "video/mp4", "media_kind": "video", "size_bytes": 10, "port": "video", "role": "video_clip", "role_index": 0}]
    gateway.discover_video, gateway.build_selected, gateway.submit, gateway.assets = discover, build, submit, assets
    binary = BytesGateway("video/mp4")
    gateway.prepare_asset, gateway.read_asset = binary.prepare_asset, binary.read_asset
    body = {"requestId": str(uuid.uuid4()), "workflowId": item["id"], "definitionVersion": 1, "definitionDigest": item["definitionDigest"],
            "imageInputs": {role: handle for role in item["referenceRoles"]}, "additionalParameters": {"motion": "One step"}}
    return a, b, gateway, factory, ca, cb, body, calls, submissions


@pytest.mark.parametrize("mode", ["i2v", "flf2v"])
def test_owned_submission_inputs_results_ranges_and_cross_account_isolation(clients, mode):
    a, b, gateway, factory, ca, cb, body, calls, submissions = setup(clients, mode)
    assert b.post("/api/generation/video/jobs", json=body, headers={"X-CSRF-TOKEN": cb}).status_code == 404
    assert not calls and not submissions
    assert a.post("/api/generation/video/jobs", json=body).status_code == 403
    response = a.post("/api/generation/video/jobs", json=body, headers={"X-CSRF-TOKEN": ca})
    assert response.status_code == 201, response.text
    execution = response.json(); assert execution["category"] == "video" and execution["operation"] == "video.generate"
    assert len(submissions) == 1
    built = next(value for key, value in (entry for entry in calls if isinstance(entry, tuple)))
    assert len(built["start_image"]) == 32 and built["start_image"] != body["imageInputs"]["start_image"]
    with factory() as db:
        row = db.get(Execution, uuid.UUID(execution["id"]))
        assert row.request_snapshot["imageInputs"] == body["imageInputs"]
        assert type(row.request_snapshot["additionalParameters"]["seed"]) is int
        assert {mapping.role for mapping in db.scalars(select(ExecutionInput).where(ExecutionInput.execution_id == row.id))} == set(body["imageInputs"])
    assert a.delete("/api/generation/inputs/" + body["imageInputs"]["start_image"], headers={"X-CSRF-TOKEN": ca}).status_code == 409
    result = a.get(f"/api/executions/{execution['id']}/result")
    assert result.status_code == 200, result.text
    asset = result.json()["assets"][0]; assert asset["previewKind"] == "video" and asset["outputRole"]["role"] == "video_clip"
    assert a.get(asset["downloadUrl"]).content == b"0123456789"
    partial = a.get(asset["previewUrl"], headers={"Range": "bytes=2-5"})
    assert partial.status_code == 206 and partial.content == b"2345"
    assert a.get("/api/assets?kind=video").json()["items"][0]["id"] == asset["id"]
    for suffix in ("", "/result", "/cancel"):
        result = b.post(f"/api/executions/{execution['id']}" + suffix, headers={"X-CSRF-TOKEN": cb}) if suffix == "/cancel" else b.get(f"/api/executions/{execution['id']}" + suffix)
        assert result.status_code == 404
    assert b.get(asset["previewUrl"]).status_code == b.get(asset["downloadUrl"]).status_code == 404
    assert b.get("/api/assets/" + asset["id"]).status_code == 404


def test_unknown_request_recovers_without_replay_after_restart_and_input_expiry(clients):
    a, b, gateway, factory, ca, _, body, calls, submissions = setup(clients)
    async def unknown(recipe): submissions.append(recipe); raise GatewayError("unavailable")
    gateway.submit = unknown
    first = a.post("/api/generation/video/jobs", json=body, headers={"X-CSRF-TOKEN": ca})
    assert first.status_code == 201 and first.json()["state"] == "submission_unknown"
    with factory() as db:
        row = db.get(ManagedInput, uuid.UUID(body["imageInputs"]["start_image"])); row.expires_at = now(); db.commit()
    with TestClient(create_app(factory, gateway, a.app.state.thumbnails)) as restarted:
        restarted.cookies.update(a.cookies)
        assert restarted.post("/api/generation/video/jobs", json=body, headers={"X-CSRF-TOKEN": ca}).json() == first.json()
        assert restarted.get("/api/generation/video/requests/" + body["requestId"]).json() == first.json()
    assert len(submissions) == 1
    assert b.get("/api/generation/video/requests/" + body["requestId"]).status_code == 404
    changed = {**body, "additionalParameters": {"motion": "Different"}}
    assert a.post("/api/generation/video/jobs", json=changed, headers={"X-CSRF-TOKEN": ca}).status_code == 409


@pytest.mark.parametrize("bad", ["expired", "roles", "digest", "dimensions"])
def test_invalid_video_submission_never_builds_or_posts(clients, bad):
    a, _, gateway, factory, ca, _, body, calls, submissions = setup(clients)
    if bad == "expired":
        with factory() as db:
            row = db.get(ManagedInput, uuid.UUID(body["imageInputs"]["start_image"])); row.expires_at = now(); db.commit()
    elif bad == "roles": body["imageInputs"]["end_image"] = body["imageInputs"]["start_image"]
    elif bad == "digest": body["definitionDigest"] = "e" * 64
    elif bad == "dimensions": body["additionalParameters"]["width"] = 769
    response = a.post("/api/generation/video/jobs", json=body, headers={"X-CSRF-TOKEN": ca})
    assert response.status_code in {409, 422}, response.text
    assert not submissions and not any(isinstance(item, tuple) for item in calls)


@pytest.mark.parametrize("change", ["session", "configuration", "reference"])
def test_response_time_changes_before_submit_fail_closed(clients, monkeypatch, change):
    a, _, gateway, factory, ca, _, body, _, submissions = setup(clients)
    async def build(selected, parameters):
        if change == "configuration": monkeypatch.setenv("STUDIO_GENERATION_NAMESPACE", "changed")
        else:
            with factory() as db:
                if change == "session": db.execute(delete(LoginSession))
                else:
                    row = db.get(ManagedInput, uuid.UUID(body["imageInputs"]["start_image"])); row.expires_at = now()
                db.commit()
        return RECIPE
    gateway.build_selected = build
    response = a.post("/api/generation/video/jobs", json=body, headers={"X-CSRF-TOKEN": ca})
    assert response.status_code in {401, 409}, response.text
    assert not submissions


@pytest.mark.parametrize("bad", ["mime", "role", "count"])
def test_completed_video_requires_one_mp4_clip_manifest(clients, bad):
    a, _, gateway, _, ca, _, body, _, _ = setup(clients)
    response = a.post("/api/generation/video/jobs", json=body, headers={"X-CSRF-TOKEN": ca})
    original = gateway.assets
    async def wrong(job):
        items = await original(job)
        if bad == "mime": items[0].update(media_kind="image", mime_type="image/png")
        elif bad == "role": items[0]["role"] = "preview"
        else: items = []
        return items
    gateway.assets = wrong
    result = a.get(f"/api/executions/{response.json()['id']}/result")
    assert result.status_code == 422, result.text


@pytest.mark.parametrize("operation", ["status", "assets", "cancel"])
def test_session_revoked_during_result_status_or_cancel_is_not_published(clients, operation):
    a, _, gateway, factory, ca, _, body, _, _ = setup(clients)
    made = a.post("/api/generation/video/jobs", json=body, headers={"X-CSRF-TOKEN": ca}).json()
    original = getattr(gateway, operation)
    async def revoke(*args):
        with factory() as db: db.execute(delete(LoginSession)); db.commit()
        return await original(*args)
    setattr(gateway, operation, revoke)
    if operation == "cancel": response = a.post(f"/api/executions/{made['id']}/cancel", headers={"X-CSRF-TOKEN": ca})
    else: response = a.get(f"/api/executions/{made['id']}" + ("/result" if operation == "assets" else ""))
    assert response.status_code == 401, response.text


def test_untrusted_submit_receipt_keeps_unknown_fence_and_does_not_bind_job(clients):
    a, _, gateway, factory, ca, _, body, _, submissions = setup(clients)
    async def wrong(recipe):
        submissions.append(recipe)
        return {"job_id": JOB, "status": "queued", "workflow_id": recipe, "operation": "image.generate"}
    gateway.submit = wrong
    response = a.post("/api/generation/video/jobs", json=body, headers={"X-CSRF-TOKEN": ca})
    assert response.status_code == 201 and response.json()["state"] == "submission_unknown"
    assert a.post("/api/generation/video/jobs", json=body, headers={"X-CSRF-TOKEN": ca}).json() == response.json()
    with factory() as db:
        assert db.get(Execution, uuid.UUID(response.json()["id"])).upstream_job_id is None
    assert len(submissions) == 1


def test_revoked_session_cannot_receive_cached_result_after_upstream_failure(clients):
    a, _, gateway, factory, ca, _, body, _, _ = setup(clients)
    made = a.post("/api/generation/video/jobs", json=body, headers={"X-CSRF-TOKEN": ca}).json()
    assert a.get(f"/api/executions/{made['id']}/result").json()["assets"]
    async def unavailable(job):
        with factory() as db: db.execute(delete(LoginSession)); db.commit()
        raise GatewayError("unavailable")
    gateway.assets = unavailable
    response = a.get(f"/api/executions/{made['id']}/result")
    assert response.status_code == 401
