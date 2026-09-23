import io
import os
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

from flamoris_studio.app import ImageRequest, create_app
from flamoris_studio.db import Asset, Base, Execution, User
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
        assert saved.storage_path == "/private/generation/output/000.png"
    for route in ("thumbnail", "content", "download"):
        path = f"/api/executions/{execution_id}/assets/{asset_id}/{route}"
        assert b.get(path).status_code == 404
        assert a.get(path).status_code == 200
    assert a.get(f"/api/executions/{execution_id}/assets/{asset_id}/download").headers[
        "content-disposition"] == 'attachment; filename="photo.png"'
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


def test_file_paths_use_output_index_not_asset_list_position(clients):
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
    assert records == {"output-first": "/private/outputs/first.png",
                       "output-second": "/private/outputs/second.png"}


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

    async def call(name, args=None):
        assert name == "assets.get" and args == {"asset_id": "internal-only"}
        return CallToolResult(content=[ImageContent(type="image", data="aGVsbG8=", mimeType="image/png")])

    monkeypatch.setattr(gateway, "_call", call)
    data, mime = await gateway.content("internal-only", 1024)
    assert data == b"hello" and mime == "image/png"
