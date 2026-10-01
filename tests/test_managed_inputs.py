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
from flamoris_studio.gateway import GatewayError
from flamoris_studio.managed_inputs import Limits, prune, quota_guard
from flamoris_studio.workflow_contract import normalize_catalog

clients = studio_clients


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
