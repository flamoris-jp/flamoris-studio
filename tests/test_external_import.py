import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from flamoris_studio.app import create_app
from flamoris_studio.db import Asset, Execution, ExternalAssetClaim, ExternalImport, LoginSession, User
from flamoris_studio.external_import import checked_outputs, checked_status, configured_binding
from test_studio import clients, register

JOB = "a" * 32
ACTOR = {"issuer": "hub.production", "subject": "client-001"}


def output(index=0, actor=ACTOR):
    return {"asset_id": f"{JOB}:{index:03d}", "job_id": JOB,
            "filename": "/private/provider/000.png", "mime_type": "image/png", "media_kind": "image",
            "size_bytes": 100, "external_provenance": actor}


def bind(monkeypatch, uid, actor=ACTOR):
    monkeypatch.setenv("STUDIO_EXTERNAL_BINDINGS", json.dumps([{"user_id": str(uid), **actor}]))
    monkeypatch.setenv("STUDIO_GENERATION_ENDPOINT", "https://generation.example.invalid/mcp")
    monkeypatch.setenv("STUDIO_GENERATION_TOKEN", "private-test-credential")


@pytest.mark.parametrize("actor", [None, {}, {**ACTOR, "subject": "foreign"},
                                   {**ACTOR, "owner_id": "spoof"}, {**ACTOR, "subject": "bad/subject"}])
def test_invalid_or_foreign_provenance_cannot_register_status_or_output(actor):
    with pytest.raises(HTTPException) as status:
        checked_status({"job_id": JOB, "status": "completed", "external_provenance": actor}, JOB, ACTOR)
    assert status.value.status_code == 404
    with pytest.raises(HTTPException) as assets:
        checked_outputs([output(actor=actor)], JOB, ACTOR)
    assert assets.value.status_code == 404


def test_bindings_reject_duplicate_subject_and_caller_owned_fields(monkeypatch):
    first, second = uuid.uuid4(), uuid.uuid4()
    bind(monkeypatch, first)
    assert configured_binding(first)[0] == ACTOR
    monkeypatch.setenv("STUDIO_EXTERNAL_BINDINGS", json.dumps([
        {"user_id": str(first), **ACTOR}, {"user_id": str(second), **ACTOR}]))
    with pytest.raises(HTTPException):
        configured_binding(first)


def setup_external(clients, monkeypatch):
    a, b, gateway, factory = clients
    csrf = register(a, "external-a@example.test")
    csrf_b = register(b, "external-b@example.test")
    with factory() as db:
        owner = db.scalar(select(User).where(User.email == "external-a@example.test")).id
    bind(monkeypatch, owner)
    calls = []

    async def status(job):
        calls.append("status")
        return {"job_id": job, "status": "completed", "external_provenance": ACTOR}

    async def assets(job):
        calls.append("assets")
        return [output(index) for index in range(2)]

    async def forbidden(job):
        pytest.fail("External import must not materialize result/binary data")

    gateway.status, gateway.assets, gateway.result = status, assets, forbidden
    return a, b, gateway, factory, csrf, csrf_b, owner, calls


def test_completed_external_import_is_owned_idempotent_metadata_only_and_restart_safe(clients, monkeypatch):
    a, b, gateway, factory, csrf, csrf_b, owner, calls = setup_external(clients, monkeypatch)
    route = "/api/generation/external-import"
    assert a.get(route).json() == {"available": True}
    assert b.get(route).json() == {"available": False}
    assert a.post(route, json={"jobId": JOB}).status_code == 403
    assert b.post(route, json={"jobId": JOB}, headers={"X-CSRF-TOKEN": csrf_b}).status_code == 403
    response = a.post(route, json={"jobId": JOB}, headers={"X-CSRF-TOKEN": csrf})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert calls == ["status", "assets", "status"]
    assert JOB not in response.text and "/private" not in response.text and "client-001" not in response.text
    assert len(saved["assets"]) == 2 and all(item["origin"] == "external" for item in saved["assets"])
    assert a.post(route, json={"jobId": JOB}, headers={"X-CSRF-TOKEN": csrf}).json() == saved
    assert a.get(f"/api/executions/{saved['id']}/result").json() == saved
    assert calls == ["status", "assets", "status"]
    for item in saved["assets"]:
        assert b.get(item["downloadUrl"]).status_code == 404
        assert a.get(item["downloadUrl"]).status_code == 200
    with TestClient(create_app(factory, gateway, a.app.state.thumbnails)) as restarted:
        restarted.cookies.update(a.cookies)
        assert restarted.get("/api/assets").json()["items"][0]["origin"] == "external"
    monkeypatch.setenv("STUDIO_EXTERNAL_BINDINGS", "[]")
    assert a.post(route, json={"jobId": JOB}, headers={"X-CSRF-TOKEN": csrf}).status_code == 403
    assert a.get(saved["assets"][0]["downloadUrl"]).status_code == 200


@pytest.mark.parametrize("failure", ["anonymous", "foreign-output", "wrong-job", "empty", "changed-status"])
def test_external_import_rejects_partial_or_changed_metadata_without_catalog_mutation(clients, monkeypatch, failure):
    a, _, gateway, factory, csrf, _, _, calls = setup_external(clients, monkeypatch)

    async def status(job):
        calls.append("status")
        return {"job_id": JOB, "status": "completed", "external_provenance": (
            None if failure == "anonymous" or failure == "changed-status" and len(calls) >= 3 else ACTOR)}

    async def assets(job):
        calls.append("assets")
        listing = [output(), output(1)]
        if failure == "foreign-output":
            listing[1]["external_provenance"] = {**ACTOR, "subject": "other-client"}
        if failure == "wrong-job":
            listing[1]["job_id"] = "b" * 32
        return [] if failure == "empty" else listing

    gateway.status, gateway.assets = status, assets
    response = a.post("/api/generation/external-import", json={"jobId": JOB},
                      headers={"X-CSRF-TOKEN": csrf})
    assert response.status_code == 404
    with factory() as db:
        assert db.scalar(select(Execution)) is None
        assert db.scalar(select(Asset)) is None and db.scalar(select(ExternalImport)) is None


def test_binding_revoked_during_import_withholds_catalog_and_reassignment_cannot_steal(clients, monkeypatch):
    a, b, gateway, factory, csrf, csrf_b, owner, calls = setup_external(clients, monkeypatch)
    original = gateway.assets

    async def revoked(job):
        result = await original(job)
        monkeypatch.setenv("STUDIO_EXTERNAL_BINDINGS", "[]")
        return result

    gateway.assets = revoked
    assert a.post("/api/generation/external-import", json={"jobId": JOB},
                  headers={"X-CSRF-TOKEN": csrf}).status_code == 403
    with factory() as db:
        assert db.scalar(select(ExternalImport)) is None
    gateway.assets = original
    bind(monkeypatch, owner)
    saved = a.post("/api/generation/external-import", json={"jobId": JOB},
                   headers={"X-CSRF-TOKEN": csrf}).json()
    with factory() as db:
        second = db.scalar(select(User).where(User.email == "external-b@example.test")).id
    bind(monkeypatch, second)
    assert b.post("/api/generation/external-import", json={"jobId": JOB},
                  headers={"X-CSRF-TOKEN": csrf_b}).status_code == 404
    assert b.get(saved["assets"][0]["downloadUrl"]).status_code == 404


def test_browser_cannot_supply_owner_or_provenance(clients, monkeypatch):
    a, _, _, _, csrf, _, _, _ = setup_external(clients, monkeypatch)
    for field in ("owner_id", "user_id", "issuer", "subject", "external_provenance"):
        assert a.post("/api/generation/external-import", json={"jobId": JOB, field: "spoof"},
                      headers={"X-CSRF-TOKEN": csrf}).status_code == 422


def test_concurrent_app_imports_claim_one_execution_and_asset_set(clients, monkeypatch):
    a, _, gateway, factory, csrf, _, _, _ = setup_external(clients, monkeypatch)
    cookies = dict(a.cookies)

    def submit(index):
        with TestClient(create_app(factory, gateway, a.app.state.thumbnails)) as worker:
            worker.cookies.update(cookies)
            return worker.post("/api/generation/external-import", json={"jobId": JOB},
                               headers={"X-CSRF-TOKEN": csrf})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    assert all(response.status_code in {200, 429} for response in responses)
    saved = a.post("/api/generation/external-import", json={"jobId": JOB},
                   headers={"X-CSRF-TOKEN": csrf}).json()
    assert all(response.json()["id"] == saved["id"] for response in responses if response.status_code == 200)
    with factory() as db:
        assert len(db.scalars(select(ExternalImport)).all()) == 1
        assert len(db.scalars(select(Execution)).all()) == 1
        assert len(db.scalars(select(Asset)).all()) == 2
        assert len(db.scalars(select(ExternalAssetClaim)).all()) == 2


def test_existing_ordinary_owner_cannot_be_reassigned_by_external_provenance(clients, monkeypatch):
    a, _, _, factory, csrf, _, _, _ = setup_external(clients, monkeypatch)
    with factory() as db:
        other = db.scalar(select(User).where(User.email == "external-b@example.test"))
        db.add(Execution(user_id=other.id, workflow="text-to-image", upstream_job_id=JOB,
                         request_snapshot={}, last_known_status="completed"))
        db.commit()
    assert a.post("/api/generation/external-import", json={"jobId": JOB},
                  headers={"X-CSRF-TOKEN": csrf}).status_code == 404
    with factory() as db:
        assert db.scalar(select(ExternalImport)) is None
        assert db.scalar(select(Asset)) is None


def test_session_revocation_during_metadata_reads_withholds_import(clients, monkeypatch):
    a, _, gateway, factory, csrf, _, owner, _ = setup_external(clients, monkeypatch)
    original = gateway.assets

    async def revoke(job):
        with factory() as db:
            for login in db.scalars(select(LoginSession).where(LoginSession.user_id == owner)):
                db.delete(login)
            db.commit()
        return await original(job)

    gateway.assets = revoke
    assert a.post("/api/generation/external-import", json={"jobId": JOB},
                  headers={"X-CSRF-TOKEN": csrf}).status_code == 401
    with factory() as db:
        assert db.scalar(select(ExternalImport)) is None
