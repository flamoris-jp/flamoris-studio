import asyncio
import hashlib
import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from test_studio import clients as studio_clients
from test_studio import image_request, register
from test_workflow_contract import descriptor

from flamoris_studio.db import Asset, Execution, ManagedInput, now
from flamoris_studio.gateway import GatewayError, GenerationGateway
from flamoris_studio.managed_inputs import Limits, prune, quota_guard
from flamoris_studio.workflow_contract import normalize_catalog

clients = studio_clients


@pytest.mark.parametrize("workflow_id", [None, 7])
def test_invalid_selected_build_response_fails_before_submission(clients, monkeypatch, workflow_id):
    a, _, gateway, factory = clients
    csrf = register(a, "invalid-build@example.test")
    selected = normalize_catalog({"descriptors": [descriptor()]})[0]
    real_gateway = GenerationGateway()

    async def discover():
        return {"available": True, "workflows": [selected]}

    async def result(name, args):
        return {**args, **({"workflow_id": workflow_id} if workflow_id is not None else {})}

    monkeypatch.setattr(real_gateway, "_json", result)
    monkeypatch.setattr(gateway, "discover", discover)
    monkeypatch.setattr(gateway, "build_selected", real_gateway.build_selected, raising=False)
    body = {**image_request(), "seed": 8, "workflowId": selected["id"], "workflowKind": "definition",
            "definitionVersion": selected["definitionVersion"], "definitionDigest": selected["definitionDigest"]}
    response = a.post("/api/generation/image/jobs", json=body, headers={"X-CSRF-TOKEN": csrf})
    assert response.status_code == 422, response.text
    assert gateway.submit_count == 0
    with factory() as db:
        execution = db.scalar(select(Execution).where(Execution.workflow == selected["id"]))
        assert execution.last_known_status == "failed"
        assert execution.upstream_job_id is None


def source(a, csrf):
    result = a.post("/api/generation/image/jobs", json=image_request(), headers={"X-CSRF-TOKEN": csrf})
    return a.get(f"/api/executions/{result.json()['id']}/result").json()["assets"][0]


def prepare_gateway(gateway):
    gateway.input_calls = []
    gateway.input_records = {}

    async def create(asset_id):
        gateway.input_calls.append(("create", asset_id))
        key = uuid.uuid4().hex
        record = {"input_id": key, "source_asset_id": asset_id, "mime_type": "image/png", "media_kind": "image",
                  "size_bytes": len(gateway.image), "sha256": hashlib.sha256(gateway.image).hexdigest(), "expires_at": time.time() + 80000}
        gateway.input_records[key] = record
        return record

    async def get(key):
        gateway.input_calls.append(("get", key))
        if key not in gateway.input_records:
            raise GatewayError("unavailable")
        return gateway.input_records[key]

    async def delete(key):
        gateway.input_calls.append(("delete", key))
        gateway.input_records.pop(key, None)
        return {"deleted": True}

    gateway.create_input, gateway.get_input, gateway.delete_input = create, get, delete


def test_owned_snapshot_cross_user_csrf_and_independent_preview(clients):
    a, b, gateway, factory = clients
    ca, cb = register(a, "input-a@example.test"), register(b, "input-b@example.test")
    asset = source(a, ca)
    prepare_gateway(gateway)
    path = "/api/generation/inputs"
    assert b.post(path, json={"assetId": asset["id"]}, headers={"X-CSRF-TOKEN": cb}).status_code == 404
    assert a.post(path, json={"assetId": asset["id"]}).status_code == 403
    assert gateway.input_calls == []
    created = a.post(path, json={"assetId": asset["id"]}, headers={"X-CSRF-TOKEN": ca})
    assert created.status_code == 201, created.text
    value = created.json()
    assert value["available"] and "upstream" not in created.text and "sha256" not in created.text
    before = list(gateway.input_calls)
    for method, suffix in [("get", ""), ("delete", ""), ("get", "/thumbnail")]:
        assert getattr(b, method)(path + "/" + value["id"] + suffix, headers={"X-CSRF-TOKEN": cb}).status_code == 404
    assert gateway.input_calls == before
    assert a.get(value["thumbnailUrl"]).status_code == 200
    a.post("/api/assets/delete", json={"ids": [asset["id"]]}, headers={"X-CSRF-TOKEN": ca})
    assert a.get(path + "/" + value["id"]).json()["available"]
    assert a.get(value["thumbnailUrl"]).status_code == 200
    with factory() as db:
        assert db.get(ManagedInput, uuid.UUID(value["id"])).accounted_bytes <= 256 * 1024


def test_quota_before_upstream_and_history_pruning_preserves_assets(clients):
    a, _, gateway, factory = clients
    csrf = register(a, "quota@example.test")
    asset = source(a, csrf)
    prepare_gateway(gateway)
    a.app.state.input_limits = Limits(user_rows=1)
    path = "/api/generation/inputs"
    item = a.post(path, json={"assetId": asset["id"]}, headers={"X-CSRF-TOKEN": csrf}).json()
    before = len(gateway.input_calls)
    assert a.post(path, json={"assetId": asset["id"]}, headers={"X-CSRF-TOKEN": csrf}).status_code == 409
    assert len(gateway.input_calls) == before
    with factory() as db:
        row = db.get(ManagedInput, uuid.UUID(item["id"]))
        row.expires_at = now() - timedelta(hours=50)
        row.terminal_at = row.expires_at
        execution = db.get(Execution, uuid.UUID(asset["executionId"]))
        execution.reference_input_id = row.id
        execution.last_known_status = "submission_unknown"
        db.commit()
        assert prune(db, a.app.state.input_thumbnails) == 0
        execution.last_known_status = "completed"
        db.commit()
        assert prune(db, a.app.state.input_thumbnails) == 1
        db.expire_all()
        assert db.get(Execution, execution.id).reference_input_id is None
        assert db.get(Asset, uuid.UUID(asset["id"])) is not None
    assert a.get(item["thumbnailUrl"]).status_code == 404


def test_selected_workflow_owner_first_both_gates_and_seed_snapshot(clients):
    a, b, gateway, factory = clients
    ca, cb = register(a, "workflow-a@example.test"), register(b, "workflow-b@example.test")
    asset = source(a, ca)
    prepare_gateway(gateway)
    attached = a.post("/api/generation/inputs", json={"assetId": asset["id"]}, headers={"X-CSRF-TOKEN": ca}).json()
    selected = normalize_catalog({"descriptors": [descriptor("img2img")]})[0]
    gate = {"available": True, "managedInputReady": True, "workflows": [selected]}
    calls = []

    async def discover():
        calls.append("discover")
        return gate

    async def build(item, parameters):
        calls.append(("build", parameters))
        return "workflow-private"

    gateway.discover, gateway.build_selected = discover, build
    body = {**image_request(), "workflowId": selected["id"], "workflowKind": "definition",
            "definitionVersion": 7, "definitionDigest": selected["definitionDigest"], "seed": 8,
            "referenceInputId": attached["id"], "denoise": 0.5}
    path = "/api/generation/image/jobs"
    assert b.post(path, json=body, headers={"X-CSRF-TOKEN": cb}).status_code == 404
    assert calls == []
    gate["managedInputReady"] = False
    assert a.post(path, json=body, headers={"X-CSRF-TOKEN": ca}).status_code == 409
    gate["managedInputReady"] = True
    assert a.post(path, json={**body, "definitionVersion": 8}, headers={"X-CSRF-TOKEN": ca}).status_code == 409
    assert a.post(path, json={**body, "require_ready": False}, headers={"X-CSRF-TOKEN": ca}).status_code == 422
    assert a.post(path, json={**body, "additionalParameters": {"source": "raw"}}, headers={"X-CSRF-TOKEN": ca}).status_code == 422
    result = a.post(path, json={**body, "seed": None}, headers={"X-CSRF-TOKEN": ca})
    assert result.status_code == 201, result.text
    with factory() as db:
        execution = db.get(Execution, uuid.UUID(result.json()["id"]))
        snapshot = execution.request_snapshot
        assert snapshot["snapshotVersion"] == 2 and 4 <= snapshot["seed"] <= 32 and snapshot["seed"] % 4 == 0
        assert "source" not in snapshot["normalizedParameters"]
        assert execution.reference_input_id == uuid.UUID(attached["id"])
    assert a.delete("/api/generation/inputs/" + attached["id"], headers={"X-CSRF-TOKEN": ca}).status_code == 409


def test_quota_lock_and_byte_quota_reject_before_upstream(clients):
    a, _, gateway, factory = clients
    csrf = register(a, "input-lock@example.test")
    asset = source(a, csrf)
    prepare_gateway(gateway)
    path = "/api/generation/inputs"
    with factory() as db:
        quota_guard(db)
        assert a.post(path, json={"assetId": asset["id"]}, headers={"X-CSRF-TOKEN": csrf}).status_code == 409
        assert gateway.input_calls == []
    a.app.state.input_limits = Limits(global_bytes=256 * 1024 - 1)
    assert a.post(path, json={"assetId": asset["id"]}, headers={"X-CSRF-TOKEN": csrf}).status_code == 409
    assert gateway.input_calls == []


def test_unknown_create_is_charged_and_never_replayed(clients):
    a, _, gateway, factory = clients
    csrf = register(a, "input-unknown@example.test")
    asset = source(a, csrf)
    prepare_gateway(gateway)
    calls = []

    async def unknown(key):
        calls.append(key)
        raise GatewayError("unavailable")

    gateway.create_input = unknown
    path = "/api/generation/inputs"
    assert a.post(path, json={"assetId": asset["id"]}, headers={"X-CSRF-TOKEN": csrf}).status_code == 503
    assert len(calls) == 1
    with factory() as db:
        row = db.scalar(select(ManagedInput))
        assert row.state == "create_unknown" and row.accounted_bytes == 256 * 1024
        assert row.upstream_input_id is None


def test_uncertain_delete_and_thumbnail_cleanup_remain_unavailable_and_accounted(clients, monkeypatch):
    a, _, gateway, factory = clients
    csrf = register(a, "input-delete@example.test")
    asset = source(a, csrf)
    prepare_gateway(gateway)
    path = "/api/generation/inputs"
    item = a.post(path, json={"assetId": asset["id"]}, headers={"X-CSRF-TOKEN": csrf}).json()

    async def unknown(key):
        raise GatewayError("unavailable")

    gateway.delete_input = unknown
    assert a.delete(path + "/" + item["id"], headers={"X-CSRF-TOKEN": csrf}).status_code == 409
    assert not a.get(path + "/" + item["id"]).json()["available"]
    real_delete = a.app.state.input_thumbnails.delete

    def failure(locator):
        raise OSError("storage busy")

    monkeypatch.setattr(a.app.state.input_thumbnails, "delete", failure)
    with factory() as db:
        row = db.get(ManagedInput, uuid.UUID(item["id"]))
        row.expires_at = row.terminal_at = now() - timedelta(hours=50)
        before = row.accounted_bytes
        db.commit()
        assert prune(db, a.app.state.input_thumbnails) == 0
        db.refresh(row)
        assert row.state == "pending_delete" and row.accounted_bytes == before
        monkeypatch.setattr(a.app.state.input_thumbnails, "delete", real_delete)
        assert prune(db, a.app.state.input_thumbnails) == 1


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled", "running", "unavailable", "mismatch", "malformed", "unknown"])
def test_expired_references_reconcile_without_browser_polling(clients, outcome):
    from flamoris_studio.managed_inputs import reconcile_expired

    a, _, gateway, factory = clients
    csrf = register(a, "reconcile@example.test")
    asset = source(a, csrf)
    prepare_gateway(gateway)
    item = a.post("/api/generation/inputs", json={"assetId": asset["id"]}, headers={"X-CSRF-TOKEN": csrf}).json()
    input_id, execution_id = uuid.UUID(item["id"]), uuid.UUID(asset["executionId"])
    with factory() as db:
        row = db.get(ManagedInput, input_id)
        row.expires_at = now() - timedelta(hours=50)
        execution = db.get(Execution, execution_id)
        execution.reference_input_id = input_id
        execution.last_known_status = "submission_unknown" if outcome == "unknown" else "queued"
        execution.completed_at = None
        if outcome == "unknown":
            execution.upstream_job_id = None
        db.commit()
    calls = []

    async def status(job_id):
        calls.append(job_id)
        # No transaction/advisory lock may remain held during remote IO.
        with factory() as db:
            quota_guard(db)
        if outcome == "unavailable":
            raise GatewayError("unavailable")
        if outcome == "mismatch":
            return {"job_id": "other-job", "status": "completed"}
        if outcome == "malformed":
            return {"job_id": job_id, "status": {"completed": True}}
        return {"job_id": job_id, "status": outcome}

    gateway.status = status
    asyncio.run(reconcile_expired(a.app))
    terminal = outcome in {"completed", "failed", "cancelled"}
    assert len(calls) == (0 if outcome == "unknown" else 1)
    with factory() as db:
        execution = db.get(Execution, execution_id)
        assert execution.last_known_status == (outcome if terminal else "submission_unknown" if outcome == "unknown" else "queued")
        assert (execution.completed_at is not None) == terminal
        assert prune(db, a.app.state.input_thumbnails) == int(terminal)
        assert (db.get(ManagedInput, input_id) is None) == terminal


def test_reconciliation_bounds_and_cursor_do_not_starve_later_jobs(clients):
    from flamoris_studio.managed_inputs import reconcile_expired

    a, _, gateway, factory = clients
    csrf = register(a, "reconcile-batch@example.test")
    asset = source(a, csrf)
    prepare_gateway(gateway)
    item = a.post("/api/generation/inputs", json={"assetId": asset["id"]}, headers={"X-CSRF-TOKEN": csrf}).json()
    with factory() as db:
        row = db.get(ManagedInput, uuid.UUID(item["id"]))
        row.expires_at = now() - timedelta(hours=50)
        for index in range(101):
            db.add(Execution(id=uuid.UUID(int=index + 1), user_id=row.owner_user_id,
                             workflow="reference", reference_input_id=row.id,
                             upstream_job_id=f"job-{index}", request_snapshot={}, last_known_status="queued"))
        db.commit()
    calls = []

    async def status(job_id):
        calls.append(job_id)
        raise GatewayError("unavailable")

    gateway.status = status
    cursor = asyncio.run(reconcile_expired(a.app))
    assert len(calls) == 100
    cursor = asyncio.run(reconcile_expired(a.app, cursor))
    assert calls[-1] == "job-100" and len(calls) == 101
    asyncio.run(reconcile_expired(a.app, cursor))
    assert calls[101] == "job-0" and len(calls) == 201


@pytest.mark.parametrize("mutation", ["mapping", "terminal"])
def test_reconciliation_does_not_overwrite_concurrent_changes(clients, mutation):
    from flamoris_studio.managed_inputs import reconcile_expired

    a, _, gateway, factory = clients
    csrf = register(a, "reconcile-race@example.test")
    asset = source(a, csrf)
    prepare_gateway(gateway)
    item = a.post("/api/generation/inputs", json={"assetId": asset["id"]}, headers={"X-CSRF-TOKEN": csrf}).json()
    execution_id = uuid.UUID(asset["executionId"])
    with factory() as db:
        row = db.get(ManagedInput, uuid.UUID(item["id"]))
        row.expires_at = now() - timedelta(hours=50)
        execution = db.get(Execution, execution_id)
        execution.reference_input_id = row.id
        execution.last_known_status = "queued"
        db.commit()

    async def status(job_id):
        with factory() as db:
            execution = db.get(Execution, execution_id)
            if mutation == "mapping":
                execution.upstream_job_id = "replacement-job"
            else:
                execution.last_known_status = "cancelled"
            db.commit()
        return {"job_id": job_id, "status": "completed"}

    gateway.status = status
    asyncio.run(reconcile_expired(a.app))
    with factory() as db:
        assert db.get(Execution, execution_id).last_known_status == ("queued" if mutation == "mapping" else "cancelled")


@pytest.mark.parametrize("editable", [False, True])
def test_selected_submission_omits_constant_controls_and_snapshots_normalized_defaults(clients, editable):
    a, _, gateway, factory = clients
    csrf = register(a, "constant-controls@example.test")
    raw = descriptor()
    if editable:
        raw["parameters"]["count"] = {"type": "integer", "role": "steps", "required": False, "default": 12}
        raw["parameters"]["guidance"] = {"type": "number", "role": "cfg", "required": False, "default": 4}
    selected = normalize_catalog({"descriptors": [raw]})[0]
    calls = []

    async def discover():
        return {"available": True, "workflows": [selected]}

    async def build(item, parameters):
        calls.append(parameters)
        return "workflow-private"

    gateway.discover, gateway.build_selected = discover, build
    body = {k:v for k,v in image_request().items() if k not in {"steps", "cfg", "seed"}}
    body.update(workflowId=selected["id"], workflowKind="definition", definitionVersion=7, definitionDigest=selected["definitionDigest"])
    response = a.post("/api/generation/image/jobs", json=body, headers={"X-CSRF-TOKEN": csrf})
    assert response.status_code == 201, response.text
    with factory() as db:
        snapshot = db.get(Execution, uuid.UUID(response.json()["id"])).request_snapshot
        assert 4 <= snapshot["seed"] <= 32 and snapshot["seed"] % 4 == 0
        if editable:
            assert snapshot["steps"] == 12 and snapshot["cfg"] == 4
            assert calls[0]["count"] == 12 and calls[0]["guidance"] == 4
        else:
            assert "steps" not in snapshot and "cfg" not in snapshot
            assert "count" not in calls[0] and "guidance" not in calls[0]
