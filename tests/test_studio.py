import io
import base64
import hashlib
import os
import time
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from flamoris_studio.app import ImageRequest, create_app
from flamoris_studio.db import Asset, Base, Execution, LoginSession, User, now
from flamoris_studio.auth import SESSION_SECONDS, hasher
from flamoris_studio.gateway import GatewayError
from flamoris_studio.gateway import GenerationGateway
from flamoris_studio.media import Thumbnails, filename, inspect_image


class FakeGateway:
    def __init__(self):
        self.submit_count = 0
        image = Image.new("RGB", (32, 24), "red")
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        self.image = buffer.getvalue()
        self.busy = False
        self.deleted = []
        self.fail_delete = False

    async def discover(self):
        return {"available": True, "templates": ["text-to-image", "text-to-image-lora"],
                "checkpoints": [{"id": "c", "name": "test"}], "loras": []}

    async def build(self, template, parameters):
        return "workflow-private"

    async def submit(self, workflow):
        self.submit_count += 1
        if self.busy:
            raise GatewayError("busy")
        return {"job_id": "private-job", "status": "queued"}

    async def status(self, job):
        return {"status": "completed"}

    async def result(self, job):
        return {"status": "completed", "files": [
            {"file": "/private/generation/output/000.png", "size_bytes": len(self.image)}]}

    async def cancel(self, job):
        return {"status": "running"}

    async def assets(self, job):
        return [{"asset_id": "private-asset", "filename": "../../photo.png",
                 "mime_type": "image/png", "media_kind": "image", "size_bytes": len(self.image),
                 "output_index": 0}]

    async def delete_asset(self, asset):
        if self.fail_delete:
            raise GatewayError("unavailable")
        self.deleted.append(asset)
        return {"deleted": True}

    async def content(self, asset, max_bytes):
        return self.image, "image/png"


@pytest.fixture
def clients(tmp_path, monkeypatch):
    dsn = os.getenv("STUDIO_DATABASE_URL")
    if not dsn:
        pytest.skip("PostgreSQL fixture requires STUDIO_DATABASE_URL")
    engine = create_engine(dsn)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setenv("STUDIO_DEV_INSECURE_COOKIE", "1")
    monkeypatch.setenv("STUDIO_ALLOW_REGISTRATION", "1")
    gateway = FakeGateway()
    app = create_app(factory, gateway, Thumbnails(str(tmp_path)))
    with TestClient(app) as client_a, TestClient(app) as client_b:
        yield client_a, client_b, gateway, factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def register(client, email):
    csrf = client.get("/api/session").json()["csrfToken"]
    response = client.post("/api/auth/register", json={"email": email, "password": "Secure-Password-123"},
                           headers={"X-CSRF-TOKEN": csrf})
    assert response.status_code == 200, response.text
    return client.get("/api/session").json()["csrfToken"]


def image_request():
    return {"positivePrompt": "a quiet stage", "negativePrompt": "", "width": 512,
            "height": 512, "steps": 20, "cfg": 7, "seed": 1, "checkpoint": "test"}


def test_image_seed_matches_browser_safe_integer_contract(clients):
    a, _, _, _ = clients
    csrf = register(a, "seed@example.test")
    maximum = 2**53 - 1

    accepted = a.post("/api/generation/image/jobs", json={**image_request(), "seed": maximum},
                      headers={"X-CSRF-TOKEN": csrf})
    assert accepted.status_code == 201, accepted.text
    result = a.get(f"/api/executions/{accepted.json()['id']}/result").json()
    asset_id = result["assets"][0]["id"]
    assert a.get(f"/api/assets/{asset_id}").json()["settings"]["seed"] == maximum

    rejected = a.post("/api/generation/image/jobs", json={**image_request(), "seed": maximum + 1},
                      headers={"X-CSRF-TOKEN": csrf})
    assert rejected.status_code == 422


def test_auto_seed_is_saved_before_submission_and_explicit_zero_is_preserved(clients, monkeypatch):
    a, _, _, factory = clients
    csrf = register(a, "auto-seed@example.test")
    monkeypatch.setattr("flamoris_studio.app.secrets.randbelow", lambda upper: 2345)
    body = image_request()
    body.pop("seed")
    response = a.post("/api/generation/image/jobs", json=body, headers={"X-CSRF-TOKEN": csrf})
    assert response.status_code == 201
    with factory() as db:
        execution = db.get(Execution, uuid.UUID(response.json()["id"]))
        assert execution.request_snapshot["seed"] == 2345
    fixed = a.post("/api/generation/image/jobs", json={**body, "seed": 0},
                   headers={"X-CSRF-TOKEN": csrf})
    assert fixed.status_code == 201
    with factory() as db:
        assert db.get(Execution, uuid.UUID(fixed.json()["id"])).request_snapshot["seed"] == 0


def test_image_preferences_are_user_owned_and_validated(clients):
    a, b, _, _ = clients
    csrf_a = register(a, "preferences-a@example.test")
    csrf_b = register(b, "preferences-b@example.test")
    path = "/api/generation/image/preferences"
    assert a.get(path).json() == {"width": 512, "height": 512, "steps": 20, "cfg": 7}
    values = {"width": 768, "height": 1152, "steps": 28, "cfg": 6.5}
    assert a.put(path, json=values, headers={"X-CSRF-TOKEN": csrf_a}).json() == values
    assert a.get(path).json() == values
    assert b.get(path).json() == {"width": 512, "height": 512, "steps": 20, "cfg": 7}
    assert b.put(path, json={**values, "width": 513}, headers={"X-CSRF-TOKEN": csrf_b}).status_code == 422
    assert b.get(path).json()["width"] == 512


def test_user_scoped_styles_and_request_snapshot(clients):
    a, b, gateway, factory = clients
    csrf_a = register(a, "style-a@example.test")
    csrf_b = register(b, "style-b@example.test")
    path = "/api/generation/image/styles"
    body = {"name": "Soft light", "positivePrompt": "tag, " * 3000, "negativePrompt": "blur, " * 2000}
    assert a.post(path, json=body).status_code == 403
    created = a.post(path, json=body, headers={"X-CSRF-TOKEN": csrf_a})
    assert created.status_code == 201, created.text
    style_id = created.json()["id"]
    assert a.get(path).json()["items"][0]["positivePrompt"] == body["positivePrompt"]
    assert b.get(path).json()["items"] == []
    assert b.get(f"{path}/{style_id}").status_code == 404
    assert b.put(f"{path}/{style_id}", json=body, headers={"X-CSRF-TOKEN": csrf_b}).status_code == 404
    assert b.post(f"{path}/{style_id}/duplicate", json={"name": "copy"}, headers={"X-CSRF-TOKEN": csrf_b}).status_code == 404
    assert b.delete(f"{path}/{style_id}", headers={"X-CSRF-TOKEN": csrf_b}).status_code == 404
    assert a.post(path, json=body, headers={"X-CSRF-TOKEN": csrf_a}).status_code == 409
    assert a.post(path, json={**body, "name": "   "}, headers={"X-CSRF-TOKEN": csrf_a}).status_code == 422
    updated = {**body, "positivePrompt": "edited"}
    assert a.put(f"{path}/{style_id}", json=updated, headers={"X-CSRF-TOKEN": csrf_a}).json()["positivePrompt"] == "edited"
    duplicate = a.post(f"{path}/{style_id}/duplicate", json={"name": "copy"}, headers={"X-CSRF-TOKEN": csrf_a})
    assert duplicate.status_code == 201 and duplicate.json()["positivePrompt"] == "edited"
    assert a.delete(f"{path}/{style_id}", headers={"X-CSRF-TOKEN": csrf_a}).status_code == 200
    assert a.get(f"{path}/{style_id}").status_code == 404

    request = {**image_request(), "sampler": "dpmpp_2m", "scheduler": "karras", "denoise": 0.75,
               "loras": [{"name": "first", "strengthModel": 0.4, "strengthClip": 0.8},
                         {"name": "second", "strengthModel": 1.2, "strengthClip": 1.0}]}
    made = a.post("/api/generation/image/jobs", json=request, headers={"X-CSRF-TOKEN": csrf_a})
    assert made.status_code == 201, made.text
    result = a.get(f"/api/executions/{made.json()['id']}/result").json()
    asset_id = result["assets"][0]["id"]
    settings = a.get(f"/api/assets/{asset_id}").json()["settings"]
    assert settings["loras"] == request["loras"]
    assert settings["sampler"] == "dpmpp_2m" and settings["scheduler"] == "karras"
    assert b.get(f"/api/assets/{asset_id}").status_code == 404
    assert ImageRequest(**request).parameters()["denoise"] == 0.75


def test_multi_user_generation_and_private_assets(clients):
    a, b, gateway, factory = clients
    csrf_a = register(a, "a@example.test")
    csrf_b = register(b, "b@example.test")
    made = a.post("/api/generation/image/jobs", json=image_request(), headers={"X-CSRF-TOKEN": csrf_a})
    assert made.status_code == 201, made.text
    execution_id = made.json()["id"]
    assert "private-job" not in made.text
    assert b.get(f"/api/executions/{execution_id}").status_code == 404
    assert b.get(f"/api/executions/{execution_id}/result").status_code == 404
    assert b.post(f"/api/executions/{execution_id}/cancel", headers={"X-CSRF-TOKEN": csrf_b}).status_code == 404
    result = a.get(f"/api/executions/{execution_id}/result")
    assert result.status_code == 200, result.text
    asset_id = result.json()["assets"][0]["id"]
    assert "private-asset" not in result.text and "private-job" not in result.text
    assert "/private/generation" not in result.text
    with factory() as db:
        saved = db.scalar(select(Asset))
        assert saved.storage_path is None
    for route in ("thumbnail", "content", "download"):
        path = f"/api/executions/{execution_id}/assets/{asset_id}/{route}"
        assert b.get(path).status_code == 404
        assert a.get(path).status_code == 200
    assert a.get(f"/api/executions/{execution_id}/assets/{asset_id}/download").headers[
        "content-disposition"] == f'attachment; filename="{result.json()["assets"][0]["displayName"]}"'
    # Reconstruct the service with the same DB: ownership and asset lookup remain durable.
    again = create_app(factory, gateway, a.app.state.thumbnails)
    with TestClient(again) as other_process:
        other_process.cookies.update(a.cookies)
        assert other_process.get(f"/api/executions/{execution_id}").status_code == 200


def test_busy_is_private_and_not_retried(clients):
    a, _, gateway, factory = clients
    csrf = register(a, "a@example.test")
    gateway.busy = True
    response = a.post("/api/generation/image/jobs", json=image_request(), headers={"X-CSRF-TOKEN": csrf})
    assert response.status_code == 409 and gateway.submit_count == 1
    assert "private-job" not in response.text and "a@example.test" not in response.text
    with factory() as db:
        assert db.scalar(select(Execution)).last_known_status == "busy"


def test_validation_and_csrf(clients):
    a, _, gateway, _ = clients
    csrf = register(a, "a@example.test")
    assert a.post("/api/generation/image/jobs", json=image_request()).status_code == 403
    request = image_request()
    request["width"] = 513
    assert a.post("/api/generation/image/jobs", json=request,
                  headers={"X-CSRF-TOKEN": csrf}).status_code == 422
    assert gateway.submit_count == 0


def test_csrf_remains_valid_across_tabs(clients):
    first, second, gateway, _ = clients
    initial_token = register(first, "tabs@example.test")
    second.cookies.update(first.cookies)
    other_tab_token = second.get("/api/session").json()["csrfToken"]
    assert initial_token == other_tab_token
    # Both tabs must be able to submit after either refreshes the session view.
    assert first.post("/api/generation/image/jobs", json=image_request(),
                      headers={"X-CSRF-TOKEN": initial_token}).status_code == 201
    assert second.post("/api/generation/image/jobs", json=image_request(),
                       headers={"X-CSRF-TOKEN": other_tab_token}).status_code == 201
    assert gateway.submit_count == 2


def test_email_change_preserves_identity_and_assets(clients):
    a, b, _, factory = clients
    csrf = register(a, "a@example.test")
    register(b, "b@example.test")
    made = a.post("/api/generation/image/jobs", json=image_request(), headers={"X-CSRF-TOKEN": csrf})
    execution_id = made.json()["id"]
    asset_id = a.get(f"/api/executions/{execution_id}/result").json()["assets"][0]["id"]
    with factory() as db:
        user_id = db.scalar(select(User).where(User.email == "a@example.test")).id
    route = "/api/account/email"
    assert a.post(route, json={"email": "New@example.test", "currentPassword": "wrong"},
                  headers={"X-CSRF-TOKEN": csrf}).status_code == 403
    assert a.post(route, json={"email": "b@example.test", "currentPassword": "Secure-Password-123"},
                  headers={"X-CSRF-TOKEN": csrf}).status_code == 409
    assert a.post(route, json={"email": "New@example.test", "currentPassword": "Secure-Password-123"}).status_code == 403
    changed = a.post(route, json={"email": "  New@Example.Test  ", "currentPassword": "Secure-Password-123"},
                     headers={"X-CSRF-TOKEN": csrf})
    assert changed.status_code == 200, changed.text
    assert a.get("/api/session").json()["userName"] == "new@example.test"
    with factory() as db:
        assert db.scalar(select(User).where(User.email == "new@example.test")).id == user_id
        assert db.get(Execution, uuid.UUID(execution_id)).user_id == user_id
        assert db.get(Asset, uuid.UUID(asset_id)).user_id == user_id
    assert a.get(f"/api/executions/{execution_id}/assets/{asset_id}/download").status_code == 200
    assert b.get(f"/api/executions/{execution_id}/assets/{asset_id}/download").status_code == 404


def test_password_change_keeps_current_session_and_revokes_others(clients):
    a, b, _, factory = clients
    csrf_a = register(a, "password@example.test")
    csrf_b = b.get("/api/session").json()["csrfToken"]
    assert b.post("/api/auth/login", json={"email": "password@example.test",
                  "password": "Secure-Password-123"}, headers={"X-CSRF-TOKEN": csrf_b}).status_code == 200
    route = "/api/account/password"
    valid = {"currentPassword": "Secure-Password-123", "newPassword": "New-Secure-Password-456",
             "confirmPassword": "New-Secure-Password-456"}
    assert a.post(route, json=valid).status_code == 403  # CSRF required
    assert a.post(route, json={**valid, "currentPassword": "wrong"},
                  headers={"X-CSRF-TOKEN": csrf_a}).status_code == 403
    assert a.post(route, json={**valid, "confirmPassword": "different-password"},
                  headers={"X-CSRF-TOKEN": csrf_a}).status_code == 422
    assert a.post(route, json={**valid, "newPassword": "short", "confirmPassword": "short"},
                  headers={"X-CSRF-TOKEN": csrf_a}).status_code == 422
    assert b.get("/api/session").json()["authenticated"]
    changed = a.post(route, json=valid, headers={"X-CSRF-TOKEN": csrf_a})
    assert changed.status_code == 200, changed.text
    assert a.get("/api/session").json()["authenticated"]
    assert b.get("/api/session").json()["authenticated"] is False
    with factory() as db:
        user = db.scalar(select(User).where(User.email == "password@example.test"))
        assert len(list(db.scalars(select(LoginSession).where(LoginSession.user_id == user.id)))) == 1
    assert b.post("/api/auth/login", json={"email": "password@example.test",
                  "password": "Secure-Password-123"}, headers={"X-CSRF-TOKEN": csrf_b}).status_code == 401
    assert b.post("/api/auth/login", json={"email": "password@example.test",
                  "password": valid["newPassword"]}, headers={"X-CSRF-TOKEN": csrf_b}).status_code == 200



def test_password_change_db_failure_rolls_back_hash_and_session_revocation(clients):
    a, b, _, factory = clients
    csrf_a = register(a, "rollback@example.test")
    csrf_b = b.get("/api/session").json()["csrfToken"]
    credentials = {"email": "rollback@example.test", "password": "Secure-Password-123"}
    assert b.post("/api/auth/login", json=credentials, headers={"X-CSRF-TOKEN": csrf_b}).status_code == 200
    with factory() as db:
        user = db.scalar(select(User).where(User.email == credentials["email"]))
        old_hash = user.password_hash
        old_sessions = set(db.scalars(select(LoginSession.token_hash).where(LoginSession.user_id == user.id)))

    with patch.object(factory.class_, "commit", side_effect=SQLAlchemyError("injected database failure")):
        response = a.post("/api/account/password", json={
            "currentPassword": credentials["password"], "newPassword": "New-Secure-Password-456",
            "confirmPassword": "New-Secure-Password-456"}, headers={"X-CSRF-TOKEN": csrf_a})
    assert response.status_code == 503
    assert "injected" not in response.text
    with factory() as db:
        user = db.scalar(select(User).where(User.email == credentials["email"]))
        assert user.password_hash == old_hash
        assert hasher.verify(user.password_hash, credentials["password"])
        assert set(db.scalars(select(LoginSession.token_hash).where(LoginSession.user_id == user.id))) == old_sessions
    assert a.get("/api/session").json()["authenticated"]
    assert b.get("/api/session").json()["authenticated"]
    with TestClient(a.app) as fresh:
        csrf = fresh.get("/api/session").json()["csrfToken"]
        assert fresh.post("/api/auth/login", json=credentials,
                          headers={"X-CSRF-TOKEN": csrf}).status_code == 200
        assert fresh.post("/api/auth/login", json={**credentials, "password": "New-Secure-Password-456"},
                          headers={"X-CSRF-TOKEN": csrf}).status_code == 401


def test_password_change_does_not_affect_another_user(clients):
    a, b, _, factory = clients
    csrf_a = register(a, "password-a@example.test")
    register(b, "password-b@example.test")
    made = a.post("/api/generation/image/jobs", json=image_request(),
                  headers={"X-CSRF-TOKEN": csrf_a})
    asset = a.get(f"/api/executions/{made.json()['id']}/result").json()["assets"][0]
    with factory() as db:
        other = db.scalar(select(User).where(User.email == "password-b@example.test"))
        other_hash = other.password_hash
        other_sessions = set(db.scalars(select(LoginSession.token_hash).where(LoginSession.user_id == other.id)))
    response = a.post("/api/account/password", json={
        "currentPassword": "Secure-Password-123", "newPassword": "New-Secure-Password-456",
        "confirmPassword": "New-Secure-Password-456"}, headers={"X-CSRF-TOKEN": csrf_a})
    assert response.status_code == 200, response.text
    with factory() as db:
        other = db.scalar(select(User).where(User.email == "password-b@example.test"))
        assert other.password_hash == other_hash
        assert set(db.scalars(select(LoginSession.token_hash).where(LoginSession.user_id == other.id))) == other_sessions
    assert b.get("/api/session").json()["authenticated"]
    assert b.get("/api/generation/image/discovery").status_code == 200
    assert b.get(asset["downloadUrl"]).status_code == 404
    assert a.get(asset["downloadUrl"]).status_code == 200

def test_persistent_cookie_and_server_session_expiry(clients):
    a, _, _, factory = clients
    register(a, "a@example.test")
    login_cookie = next(cookie for cookie in a.cookies.jar if cookie.name == "flamoris.studio")
    assert login_cookie.expires is not None and login_cookie.expires - time.time() > SESSION_SECONDS - 60
    with factory() as db:
        login = db.scalar(select(LoginSession))
        assert SESSION_SECONDS - 60 < (login.expires_at - now()).total_seconds() <= SESSION_SECONDS
        login.expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert not a.get("/api/session").json()["authenticated"]
    assert a.get("/api/generation/image/discovery").status_code == 401


def test_bounded_download_verifies_chunks_and_owner(clients, monkeypatch):
    a, b, gateway, _ = clients
    csrf = register(a, "a@example.test")
    register(b, "b@example.test")
    monkeypatch.setenv("STUDIO_MAX_ASSET_BYTES", "1")
    made = a.post("/api/generation/image/jobs", json=image_request(), headers={"X-CSRF-TOKEN": csrf})
    asset = a.get(f"/api/executions/{made.json()['id']}/result").json()["assets"][0]
    calls = []

    async def prepare(asset_id):
        calls.append(("prepare", asset_id))
        return {"asset_id": asset_id, "mime_type": "image/png", "size_bytes": len(gateway.image),
                "sha256": hashlib.sha256(gateway.image).hexdigest(),
                "chunk_bytes": 32, "transfer_version": 1}

    async def read(asset_id, digest, offset, length):
        calls.append(("read", offset))
        if offset == 32 and calls.count(("read", 32)) == 1:
            raise GatewayError("unavailable")
        data = gateway.image[offset:offset + length]
        return {"asset_id": asset_id, "sha256": digest, "size_bytes": len(gateway.image),
                "offset": offset, "data_base64": base64.b64encode(data).decode(),
                "chunk_sha256": hashlib.sha256(data).hexdigest(),
                "next_offset": offset + len(data), "eof": offset + len(data) == len(gateway.image)}

    gateway.prepare_asset = prepare
    gateway.read_asset = read
    assert b.get(asset["downloadUrl"]).status_code == 404
    assert calls == []
    response = a.get(asset["downloadUrl"])
    assert response.status_code == 200 and response.content == gateway.image
    assert len(calls) > 2
    assert calls.count(("read", 32)) == 2
    assert response.headers["content-length"] == str(len(gateway.image))


def test_medium_image_uses_bounded_transfer_for_all_routes(clients):
    a, b, gateway, _ = clients
    csrf = register(a, "medium@example.test")
    register(b, "other-medium@example.test")
    image = Image.frombytes("RGB", (500, 500), os.urandom(500 * 500 * 3))
    output = io.BytesIO()
    image.save(output, "PNG")
    gateway.image = output.getvalue()
    assert 512 * 1024 < len(gateway.image) < 1024 * 1024
    calls = []

    async def native(*args):
        raise AssertionError("image must not enter one large SSE event")

    async def prepare(asset_id):
        calls.append("prepare")
        return {"asset_id": asset_id, "mime_type": "image/png", "size_bytes": len(gateway.image),
                "sha256": hashlib.sha256(gateway.image).hexdigest(),
                "chunk_bytes": 256 * 1024, "transfer_version": 1}

    async def read(asset_id, digest, offset, length):
        calls.append(offset)
        data = gateway.image[offset:offset + length]
        return {"asset_id": asset_id, "sha256": digest, "size_bytes": len(gateway.image),
                "offset": offset, "data_base64": base64.b64encode(data).decode(),
                "chunk_sha256": hashlib.sha256(data).hexdigest(),
                "next_offset": offset + len(data), "eof": offset + len(data) == len(gateway.image)}

    gateway.content = native
    gateway.prepare_asset = prepare
    gateway.read_asset = read
    made = a.post("/api/generation/image/jobs", json=image_request(), headers={"X-CSRF-TOKEN": csrf})
    asset = a.get(f"/api/executions/{made.json()['id']}/result").json()["assets"][0]
    for route in ("previewUrl", "thumbnailUrl", "downloadUrl"):
        assert b.get(asset[route]).status_code == 404
        response = a.get(asset[route])
        assert response.status_code == 200, response.text[:200] if response.status_code != 200 else ""
        if route != "thumbnailUrl":
            assert response.content == gateway.image
    assert calls.count("prepare") == 3


def test_metadata_listing_does_not_fetch_provider_paths(clients):
    client, _, gateway, factory = clients
    csrf = register(client, "order@example.test")

    async def result(job):
        return {"status": "completed", "files": [
            {"file": "/private/outputs/first.png", "size_bytes": len(gateway.image)},
            {"file": "/private/outputs/second.png", "size_bytes": len(gateway.image)}]}

    async def assets(job):
        return [
            {"asset_id": "output-second", "filename": "second.png", "mime_type": "image/png",
             "media_kind": "image", "output_index": 1},
            {"asset_id": "output-first", "filename": "first.png", "mime_type": "image/png",
             "media_kind": "image", "output_index": 0},
        ]

    gateway.result = result
    gateway.assets = assets
    created = client.post("/api/generation/image/jobs", json=image_request(), headers={"X-CSRF-TOKEN": csrf})
    execution_id = created.json()["id"]
    response = client.get(f"/api/executions/{execution_id}/result")
    assert response.status_code == 200
    assert "/private/outputs/" not in response.text
    with factory() as db:
        records = {asset.upstream_asset_id: asset.storage_path for asset in db.scalars(select(Asset))}
    assert records == {"output-first": None, "output-second": None}


def test_fresh_migration_chain_is_independent_of_live_metadata(clients):
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory

    _, _, _, factory = clients
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    revisions = list(reversed(list(ScriptDirectory.from_config(config).walk_revisions())))
    assert revisions

    def forbidden(*args, **kwargs):
        raise AssertionError("historical migrations must not use live Base metadata")

    schema = "migration_" + uuid.uuid4().hex
    with factory.kw["bind"].begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        context = MigrationContext.configure(conn)
        with Operations.context(context), \
             patch.object(Base.metadata, "create_all", side_effect=forbidden), \
             patch.object(Base.metadata, "drop_all", side_effect=forbidden):
            for revision in revisions:
                revision.module.upgrade()
            assert conn.execute(text("SELECT to_regclass('users')")).scalar() == "users"
            assert conn.execute(text("SELECT to_regclass('assets')")).scalar() == "assets"
            for revision in reversed(revisions):
                revision.module.downgrade()
            assert conn.execute(text("SELECT to_regclass('users')")).scalar() is None
        conn.execute(text(f'DROP SCHEMA "{schema}"'))


def test_media_bounds():
    assert filename("../../danger name.png") == "dangername.png"
    assert ImageRequest(**image_request()).parameters()["positive_prompt"] == "a quiet stage"
    with pytest.raises(ValueError):
        inspect_image(b"bad", "image/png")


@pytest.mark.asyncio
async def test_gateway_normalizes_sdk_v2_result(monkeypatch):
    from mcp.types import CallToolResult, ImageContent
    gateway = GenerationGateway()

    async def call(name, args=None, timeout=45):
        assert name == "assets.get" and args == {"asset_id": "internal-only"}
        return CallToolResult(content=[ImageContent(type="image", data="aGVsbG8=", mimeType="image/png")])

    monkeypatch.setattr(gateway, "_call", call)
    data, mime = await gateway.content("internal-only", 1024)
    assert data == b"hello" and mime == "image/png"


def test_gallery_detail_and_deletion_are_private_and_do_not_reimport(clients):
    a, b, gateway, factory = clients
    csrf_a = register(a, "gallery-a@example.test")
    csrf_b = register(b, "gallery-b@example.test")
    made = a.post("/api/generation/image/jobs", json=image_request(), headers={"X-CSRF-TOKEN": csrf_a})
    execution_id = made.json()["id"]
    asset_id = a.get(f"/api/executions/{execution_id}/result").json()["assets"][0]["id"]
    listed = a.get("/api/assets").json()
    assert listed["items"][0]["id"] == asset_id
    assert listed["items"][0]["hasThumbnail"] is False
    assert a.get(listed["items"][0]["thumbnailUrl"]).status_code == 200
    assert a.get(f"/api/assets/{asset_id}").json()["settings"]["positivePrompt"] == "a quiet stage"
    assert b.get("/api/assets").json()["items"] == []
    assert b.get(f"/api/assets/{asset_id}").status_code == 404
    assert a.post("/api/assets/delete", json={"ids": [asset_id]}).status_code == 403
    foreign = b.post("/api/assets/delete", json={"ids": [asset_id]}, headers={"X-CSRF-TOKEN": csrf_b})
    assert foreign.json()["results"] == [{"id": asset_id, "deleted": False, "error": "not_found"}]
    assert gateway.deleted == []

    gateway.fail_delete = True
    failed = a.post("/api/assets/delete", json={"ids": [asset_id]}, headers={"X-CSRF-TOKEN": csrf_a})
    assert failed.json()["results"][0]["deleted"] is False
    assert a.get("/api/assets").json()["items"][0]["id"] == asset_id
    gateway.fail_delete = False
    deleted = a.post("/api/assets/delete", json={"ids": [asset_id]}, headers={"X-CSRF-TOKEN": csrf_a})
    assert deleted.json()["results"] == [{"id": asset_id, "deleted": True}]
    assert gateway.deleted == ["private-asset"]
    assert a.get("/api/assets").json()["items"] == []
    assert a.get(f"/api/assets/{asset_id}").status_code == 404
    assert a.get(f"/api/executions/{execution_id}/assets/{asset_id}/download").status_code == 404
    assert a.get(f"/api/executions/{execution_id}/result").json()["assets"] == []
    assert a.post("/api/assets/delete", json={"ids": [asset_id]}, headers={"X-CSRF-TOKEN": csrf_a}).json()["results"][0]["deleted"]
    assert gateway.deleted == ["private-asset"]
    with factory() as db:
        record = db.get(Asset, uuid.UUID(asset_id))
        assert record.availability == "deleted" and record.thumbnail_locator is None


def test_bulk_delete_mixed_ownership_is_individual(clients):
    a, b, gateway, _ = clients
    csrf_a = register(a, "bulk-a@example.test")
    csrf_b = register(b, "bulk-b@example.test")
    made_a = a.post("/api/generation/image/jobs", json=image_request(), headers={"X-CSRF-TOKEN": csrf_a}).json()
    made_b = b.post("/api/generation/image/jobs", json=image_request(), headers={"X-CSRF-TOKEN": csrf_b}).json()
    asset_a = a.get(f"/api/executions/{made_a['id']}/result").json()["assets"][0]["id"]
    asset_b = b.get(f"/api/executions/{made_b['id']}/result").json()["assets"][0]["id"]
    response = a.post("/api/assets/delete", json={"ids": [asset_a, asset_b]},
                      headers={"X-CSRF-TOKEN": csrf_a}).json()["results"]
    assert response == [{"id": asset_a, "deleted": True},
                        {"id": asset_b, "deleted": False, "error": "not_found"}]
    assert b.get(f"/api/assets/{asset_b}").status_code == 200
    assert a.get("/api/assets").json()["items"] == []


def test_thumbnail_cleanup_error_is_retryable_after_upstream_delete(clients, monkeypatch):
    a, _, gateway, factory = clients
    csrf = register(a, "cleanup@example.test")
    execution_id = a.post("/api/generation/image/jobs", json=image_request(),
                          headers={"X-CSRF-TOKEN": csrf}).json()["id"]
    asset_id = a.get(f"/api/executions/{execution_id}/result").json()["assets"][0]["id"]
    assert a.get(f"/api/executions/{execution_id}/assets/{asset_id}/thumbnail").status_code == 200
    thumbnails = a.app.state.thumbnails
    original = thumbnails.delete
    attempts = []

    def fail_once(locator):
        attempts.append(locator)
        if len(attempts) == 1:
            raise OSError("thumbnail directory unavailable")
        return original(locator)

    monkeypatch.setattr(thumbnails, "delete", fail_once)
    response = a.post("/api/assets/delete", json={"ids": [asset_id]},
                      headers={"X-CSRF-TOKEN": csrf})
    assert response.json()["results"] == [{"id": asset_id, "deleted": True}]
    with factory() as db:
        assert db.get(Asset, uuid.UUID(asset_id)).thumbnail_locator is not None
    assert gateway.deleted == ["private-asset"]
    retry = a.post("/api/assets/delete", json={"ids": [asset_id]},
                   headers={"X-CSRF-TOKEN": csrf})
    assert retry.json()["results"] == [{"id": asset_id, "deleted": True}]
    assert gateway.deleted == ["private-asset"]
    assert len(attempts) == 2
    with factory() as db:
        record = db.get(Asset, uuid.UUID(asset_id))
        assert record.availability == "deleted" and record.thumbnail_locator is None


def test_large_asset_catalog_is_independent_of_binary_and_survives_restart(clients):
    a, b, gateway, factory = clients
    csrf = register(a, 'large@example.test')
    register(b, 'other@example.test')
    async def forbidden(*args, **kwargs):
        raise AssertionError('metadata sync attempted binary materialization')
    async def large_assets(job):
        return [{'asset_id': 'large-asset', 'filename': 'large.png', 'mime_type': 'image/png',
                 'media_kind': 'image', 'size_bytes': 80 * 1024 * 1024}]
    async def transfer_limit(asset_id):
        raise GatewayError("asset_too_large")
    gateway.result = forbidden
    gateway.content = forbidden
    gateway.assets = large_assets
    gateway.prepare_asset = transfer_limit
    created = a.post('/api/generation/image/jobs', json=image_request(), headers={'X-CSRF-TOKEN': csrf})
    execution_id = created.json()['id']
    result = a.get(f'/api/executions/{execution_id}/result')
    assert result.status_code == 200
    asset = result.json()['assets'][0]
    assert asset['sizeBytes'] == 80 * 1024 * 1024
    assert not asset['hasThumbnail']
    assert a.get('/api/assets').json()['items'][0]['id'] == asset['id']
    assert b.get(asset['downloadUrl']).status_code == 404
    rejected = a.get(asset['downloadUrl'])
    assert rejected.status_code == 413
    assert rejected.json()['error'] == 'asset_too_large'
    assert a.get(asset['thumbnailUrl']).status_code == 413
    gateway.status = forbidden
    again = create_app(factory, gateway, a.app.state.thumbnails)
    with TestClient(again) as restarted:
        restarted.cookies.update(a.cookies)
        assert restarted.get(f'/api/executions/{execution_id}/result').status_code == 200
        assert restarted.get('/api/assets').json()['items'][0]['id'] == asset['id']
    with factory() as db:
        assert len(list(db.scalars(select(Asset)))) == 1


def test_catalog_sync_retries_after_timeout_and_thumbnail_disk_failure(clients):
    a, _, gateway, _ = clients
    csrf = register(a, 'retry@example.test')
    created = a.post('/api/generation/image/jobs', json=image_request(), headers={'X-CSRF-TOKEN': csrf})
    endpoint = f"/api/executions/{created.json()['id']}/result"
    original = gateway.assets
    async def unavailable(*args):
        raise GatewayError('unavailable')
    gateway.assets = unavailable
    assert a.get(endpoint).status_code == 503
    gateway.assets = original
    result = a.get(endpoint)
    assert result.status_code == 200
    asset = result.json()['assets'][0]
    with patch.object(a.app.state.thumbnails, 'save', side_effect=OSError('disk full')):
        assert a.get(asset['thumbnailUrl']).status_code == 503
    gateway.assets = unavailable
    cached = a.get(endpoint)
    assert cached.status_code == 200
    assert cached.json()['catalogSync'] == 'unavailable'
    assert a.get('/api/assets').json()['items'][0]['id'] == asset['id']


@pytest.mark.asyncio
async def test_gateway_translates_upstream_size_limit_without_leaking_details():
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    gateway = GenerationGateway()
    class Session:
        async def call_tool(self, *args, **kwargs):
            return SimpleNamespace(is_error=True, content=[SimpleNamespace(
                text='ComfyUI output exceeds the 64 MiB download limit /private/path')])
    @asynccontextmanager
    async def connection():
        yield Session()
    gateway._connection = connection
    with pytest.raises(GatewayError) as error:
        await gateway.content('asset', 64 * 1024 * 1024)
    assert error.value.code == 'asset_too_large'
    assert '/private' not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["assets.prepare", "assets.read"])
async def test_gateway_identifies_only_exact_hub_missing_transfer_tool(name):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    gateway = GenerationGateway()

    class Session:
        async def call_tool(self, called, args, **kwargs):
            assert called == name
            return SimpleNamespace(is_error=True, content=[
                SimpleNamespace(text=f"Unknown tool: {called}")])

    @asynccontextmanager
    async def connection():
        yield Session()

    gateway._connection = connection
    with pytest.raises(GatewayError) as error:
        await gateway._call(name)
    assert error.value.code == "transfer_unavailable"

    class OtherSession:
        async def call_tool(self, called, args, **kwargs):
            return SimpleNamespace(is_error=True, content=[
                SimpleNamespace(text=f"Unknown tool: {called} /private/path")])

    @asynccontextmanager
    async def other_connection():
        yield OtherSession()

    gateway._connection = other_connection
    with pytest.raises(GatewayError) as other_error:
        await gateway._call(name)
    assert other_error.value.code == "upstream_failure"


@pytest.mark.parametrize("missing", ["assets.prepare", "assets.read"])
def test_missing_transfer_capability_has_safe_error_on_asset_routes(clients, missing):
    a, b, gateway, _ = clients
    csrf = register(a, "transfer-missing@example.test")
    register(b, "transfer-other@example.test")

    async def assets(job):
        return [{"asset_id": "private-asset", "filename": "photo.png",
                 "mime_type": "image/png", "media_kind": "image",
                 "size_bytes": None, "output_index": 0}]

    calls = []

    async def prepare(asset_id):
        calls.append("assets.prepare")
        if missing == "assets.prepare":
            raise GatewayError("transfer_unavailable")
        return {"asset_id": asset_id, "mime_type": "image/png", "size_bytes": len(gateway.image),
                "sha256": hashlib.sha256(gateway.image).hexdigest(),
                "chunk_bytes": 256 * 1024, "transfer_version": 1}

    async def read(asset_id, digest, offset, length):
        calls.append("assets.read")
        raise GatewayError("transfer_unavailable")

    gateway.assets = assets
    gateway.prepare_asset = prepare
    gateway.read_asset = read
    made = a.post("/api/generation/image/jobs", json=image_request(), headers={"X-CSRF-TOKEN": csrf})
    asset = a.get(f"/api/executions/{made.json()['id']}/result").json()["assets"][0]
    for route in ("previewUrl", "thumbnailUrl", "downloadUrl"):
        before = len(calls)
        assert b.get(asset[route]).status_code == 404
        assert len(calls) == before
        response = a.get(asset[route])
        assert response.status_code == 503
        assert response.json() == {"error": "transfer_unavailable",
                                   "message": "Asset transfer is unavailable. The service deployment is incomplete."}
        assert "private-asset" not in response.text
    assert calls.count(missing) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [10, 24])
async def test_gallery_burst_queues_bounded_thumbnails_and_cached_reads_bypass_slots(clients, count):
    import asyncio
    import httpx

    a, b, gateway, factory = clients
    csrf = register(a, 'preview-burst@example.test')
    register(b, 'preview-other@example.test')
    made = a.post('/api/generation/image/jobs', json=image_request(), headers={'X-CSRF-TOKEN': csrf})
    execution_id = uuid.UUID(made.json()['id'])
    ids = []
    with factory() as db:
        execution = db.get(Execution, execution_id)
        for i in range(count):
            asset = Asset(user_id=execution.user_id, execution_id=execution_id,
                          upstream_asset_id=f'burst-{i}', storage_locator=f'burst-{i}',
                          display_name=f'{i}.png', media_kind='image', mime_type='image/png', size_bytes=None)
            db.add(asset)
            db.flush()
            ids.append(asset.id)
        db.commit()
    digest = hashlib.sha256(gateway.image).hexdigest()
    prepared = []
    active = peak = 0

    async def prepare(asset_id):
        nonlocal active, peak
        assert active == 0, 'Generation rejects concurrent prepare calls'
        active += 1
        peak = max(peak, active)
        prepared.append(asset_id)
        await asyncio.sleep(0.02)
        active -= 1
        return {'asset_id': asset_id, 'mime_type': 'image/png', 'size_bytes': len(gateway.image),
                'sha256': digest, 'chunk_bytes': 256 * 1024, 'transfer_version': 1}

    async def read(asset_id, sha256, offset, length):
        data = gateway.image[offset:offset + length]
        return {'asset_id': asset_id, 'sha256': sha256, 'offset': offset,
                'size_bytes': len(gateway.image), 'data_base64': base64.b64encode(data).decode(),
                'chunk_sha256': hashlib.sha256(data).hexdigest(),
                'next_offset': offset + len(data), 'eof': offset + len(data) == len(gateway.image)}

    gateway.prepare_asset, gateway.read_asset = prepare, read
    urls = [f'/api/executions/{execution_id}/assets/{asset_id}/thumbnail' for asset_id in ids]
    assert b.get(urls[0]).status_code == 404
    assert not prepared
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=a.app),
                                 base_url='http://testserver', cookies=dict(a.cookies)) as client:
        responses = await asyncio.gather(*(client.get(url) for url in urls))
        assert [response.status_code for response in responses] == [200] * count
        assert all(response.headers['content-type'] == 'image/webp' for response in responses)
        assert len(prepared) == count and peak == 1
        slots = a.app.state.download_slots
        await slots.acquire()
        await slots.acquire()
        try:
            assert (await client.get(urls[0])).status_code == 200
            assert len(prepared) == count
        finally:
            slots.release()
            slots.release()
    assert a.app.state.preview_admission.pending == 0


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("stage", ["discover", "build", "submit"])
@pytest.mark.parametrize("change", ["session", "route"])
def test_image_admission_and_publication_recheck_owner_and_route(clients, monkeypatch, selected, stage, change):
    from sqlalchemy import delete
    from flamoris_studio.workflow_contract import normalize_catalog
    from test_workflow_contract import descriptor

    client, _, gateway, factory = clients
    csrf = register(client, "image-fence@example.test")
    item = normalize_catalog({"descriptors": [descriptor()]})[0]
    body = {**image_request(), "seed": 8}
    if selected:
        body.update(workflowId=item["id"], workflowKind="builtin")

    def invalidate():
        if change == "session":
            with factory() as db:
                db.execute(delete(LoginSession))
                db.commit()
        else:
            monkeypatch.setenv("STUDIO_GENERATION_NAMESPACE", "changed")

    async def discover():
        if stage == "discover":
            invalidate()
        return {"available": True, "templates": ["text-to-image"], "workflows": [item]}

    async def build(*args):
        if stage == "build":
            invalidate()
        return "built-recipe"

    async def submit(workflow):
        gateway.submit_count += 1
        if stage == "submit":
            invalidate()
        return {"job_id": "accepted-job", "status": "queued"}

    gateway.discover, gateway.build, gateway.build_selected, gateway.submit = discover, build, build, submit
    response = client.post("/api/generation/image/jobs", json=body, headers={"X-CSRF-TOKEN": csrf})
    assert response.status_code == (401 if change == "session" else 409), response.text
    assert gateway.submit_count == (1 if stage == "submit" else 0)
    with factory() as db:
        execution = db.scalar(select(Execution))
        if stage == "submit":
            assert execution.upstream_job_id == "accepted-job" and execution.last_known_status == "queued"
        elif selected and stage == "build":
            assert execution.upstream_job_id is None and execution.last_known_status == "failed"
        else:
            assert execution is None


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("job", [None, {"job_id": 7, "status": "queued"},
                                {"job_id": "bad\x00id", "status": "queued"},
                                {"job_id": "../bad", "status": "queued"},
                                {"job_id": "accepted", "status": {"completed": True}},
                                {"job_id": "accepted", "status": "not-a-state"}])
def test_malformed_image_acknowledgement_preserves_unknown_submission(clients, selected, job):
    from flamoris_studio.workflow_contract import normalize_catalog
    from test_workflow_contract import descriptor

    client, _, gateway, factory = clients
    csrf = register(client, "image-ack@example.test")
    body = {**image_request(), "seed": 8}
    item = normalize_catalog({"descriptors": [descriptor()]})[0]
    if selected:
        body.update(workflowId=item["id"], workflowKind="builtin")

    async def discover():
        return {"available": True, "templates": ["text-to-image"], "workflows": [item]}

    async def build(*args):
        return "built-recipe"

    async def submit(recipe):
        gateway.submit_count += 1
        return job

    gateway.discover, gateway.build, gateway.build_selected, gateway.submit = discover, build, build, submit
    response = client.post("/api/generation/image/jobs", json=body, headers={"X-CSRF-TOKEN": csrf})
    assert response.status_code == 502, response.text
    assert gateway.submit_count == 1
    with factory() as db:
        execution = db.scalar(select(Execution))
        assert execution.upstream_job_id is None and execution.last_known_status == "submission_unknown"
