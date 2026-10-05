import asyncio
import base64
import hashlib
import time
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from mcp.types import CallToolResult, TextContent
from sqlalchemy import delete, select
from test_studio import clients as studio_clients
from test_studio import image_request, register
from test_managed_inputs import prepare_gateway

from flamoris_studio.db import Execution, LoginSession, ManagedInput, now
from flamoris_studio.gateway import GatewayError, GenerationGateway
from flamoris_studio.input_uploads import publish_upload
from flamoris_studio.managed_inputs import Limits

clients = studio_clients
PATH = "/api/generation/inputs/upload"


@pytest.mark.asyncio
async def test_failed_upload_compensation_is_bounded_and_preserves_unknown_charge(monkeypatch):
    bound = asyncio.timeout
    monkeypatch.setattr("flamoris_studio.input_uploads.asyncio.timeout", lambda seconds: bound(0.01))
    calls = []
    async def upload(*args):
        calls.append("upload")
        raise GatewayError("unavailable")
    async def remove(*args):
        calls.append("delete")
        await asyncio.Event().wait()
    row = SimpleNamespace(id=uuid.uuid4(), upstream_input_id=uuid.uuid4().hex, mime_type="image/png",
                          expires_at=None, accounted_bytes=300000)
    db = Mock(); db.get.return_value = row
    app = SimpleNamespace(state=SimpleNamespace(gateway=SimpleNamespace(upload_input=upload, delete_input=remove)))
    async with bound(1):
        with pytest.raises(GatewayError):
            await publish_upload(app, db, row, b"x")
    assert row.state == "create_unknown" and row.accounted_bytes == 300000
    assert calls == ["upload", "delete"]


@pytest.mark.asyncio
@pytest.mark.parametrize("namespace", ["", "generation"])
async def test_gateway_chunks_private_image_on_one_connection_without_retry(monkeypatch, namespace):
    monkeypatch.setenv("STUDIO_GENERATION_NAMESPACE", namespace)
    data, key, calls, connections = b"x" * (512 * 1024 + 1), uuid.uuid4().hex, [], []
    prefix = namespace + "." if namespace else ""
    assembled = bytearray()
    async def call(name, args, read_timeout_seconds):
        assert read_timeout_seconds == 15
        assert args["upload_id"] == key
        calls.append(name)
        if name.endswith("begin"):
            assert args["size_bytes"] == len(data) and args["sha256"] == hashlib.sha256(data).hexdigest()
            raw = {"upload_id": key, "offset": 0}
        elif name.endswith("write"):
            part = base64.b64decode(args["data_base64"], validate=True)
            assert 0 < len(part) <= 128 * 1024 and args["offset"] == len(assembled)
            assert args["chunk_sha256"] == hashlib.sha256(part).hexdigest()
            assembled.extend(part)
            raw = {"upload_id": key, "offset": len(assembled)}
        else:
            assert bytes(assembled) == data
            raw = {"input_id": key}
        return CallToolResult(content=[], structured_content=raw)
    @asynccontextmanager
    async def connection():
        connections.append(True)
        yield SimpleNamespace(call_tool=call)
    gateway = GenerationGateway()
    monkeypatch.setattr(gateway, "_connection", connection)
    assert await gateway.upload_input(key, data, "image/png") == {"input_id": key}
    assert len(connections) == 1
    assert calls == [prefix + "inputs.upload.begin", *[prefix + "inputs.upload.write"] * 5, prefix + "inputs.upload.finish"]


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [True, -1, 5, "0"])
async def test_gateway_rejects_invalid_upload_cursor_without_writes(monkeypatch, offset):
    key, calls = uuid.uuid4().hex, []
    async def call(name, args, **kwargs):
        calls.append(name)
        return CallToolResult(content=[], structured_content={"upload_id": key, "offset": offset})
    @asynccontextmanager
    async def connection():
        yield SimpleNamespace(call_tool=call)
    gateway = GenerationGateway()
    monkeypatch.setattr(gateway, "_connection", connection)
    with pytest.raises(GatewayError):
        await gateway.upload_input(key, b"x", "image/png")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_gateway_unknown_upload_never_replays_or_echoes_private_payload(monkeypatch):
    calls = []
    async def call(name, args, **kwargs):
        calls.append(name)
        return CallToolResult(content=[TextContent(type="text", text="private filename/image content")], is_error=True)
    @asynccontextmanager
    async def connection():
        yield SimpleNamespace(call_tool=call)
    gateway = GenerationGateway()
    monkeypatch.setattr(gateway, "_connection", connection)
    with pytest.raises(GatewayError) as error:
        await gateway.upload_input(uuid.uuid4().hex, b"x", "image/png")
    assert "private" not in str(error.value) and len(calls) == 1


def uploading(gateway, factory):
    prepare_gateway(gateway)
    async def upload(key, data, mime):
        # The random upstream identity and complete byte charge are durable
        # before any upload RPC, including a lost begin/finish response.
        with factory() as db:
            row = db.scalar(select(ManagedInput).where(ManagedInput.upstream_input_id == key))
            assert row is not None and row.source_asset_id is None and row.source_upstream_id is None
            assert row.accounted_bytes >= len(data) + 256 * 1024
            assert row.checksum == hashlib.sha256(data).hexdigest()
        gateway.input_calls.append(("upload", key))
        raw = {"input_id": key, "source_asset_id": None, "source_kind": "upload", "mime_type": mime,
               "media_kind": "image", "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
               "expires_at": time.time() + 80000}
        gateway.input_records[key] = raw
        return raw
    gateway.upload_input = upload
    return upload


def test_upload_owner_csrf_private_preview_and_no_generated_asset(clients):
    a, b, gateway, factory = clients
    ca, cb = register(a, "upload-a@example.test"), register(b, "upload-b@example.test")
    uploading(gateway, factory)
    assert a.post(PATH, content=gateway.image, headers={"Content-Type": "image/png"}).status_code == 403
    assert gateway.input_calls == []
    response = a.post(PATH, content=gateway.image, headers={"Content-Type": "image/png", "X-CSRF-TOKEN": ca})
    assert response.status_code == 201, response.text
    value = response.json()
    assert value["available"] and value["sourceAssetId"] is None
    assert "sha256" not in response.text and "upstream" not in response.text
    assert response.headers["Cache-Control"] == "private, no-store"
    path = "/api/generation/inputs/" + value["id"]
    before = list(gateway.input_calls)
    for method, suffix in [("get", ""), ("delete", ""), ("get", "/thumbnail")]:
        assert getattr(b, method)(path + suffix, headers={"X-CSRF-TOKEN": cb}).status_code == 404
    assert gateway.input_calls == before
    assert a.get(value["thumbnailUrl"]).status_code == 200
    assert a.get("/api/assets").json()["items"] == []
    with factory() as db:
        row = db.get(ManagedInput, uuid.UUID(value["id"]))
        assert row.accounted_bytes >= len(gateway.image) and row.upstream_input_id not in response.text
    assert a.delete(path, headers={"X-CSRF-TOKEN": ca}).status_code == 200
    assert not a.get(path).json()["available"]
    # Retain the owner-only historical preview during the existing 24h grace.
    assert a.get(value["thumbnailUrl"]).status_code == 200
    with factory() as db:
        row = db.get(ManagedInput, uuid.UUID(value["id"]))
        row.terminal_at = now() - timedelta(hours=25)
        db.commit()
    assert a.get(value["thumbnailUrl"]).status_code == 404


@pytest.mark.parametrize("mime,length,content,status", [
    ("image/svg+xml", None, b"x", 415), ("image/png", "8388609", b"x", 413),
    ("image/png", "9" * 5000, b"x", 413), ("image/png", None, b"", 413),
    ("image/png", "5", b"x", 422),
])
def test_upload_body_bound_and_type_before_upstream(clients, mime, length, content, status):
    a, _, gateway, factory = clients
    csrf = register(a, "upload-limit@example.test")
    uploading(gateway, factory)
    headers = {"Content-Type": mime, "X-CSRF-TOKEN": csrf}
    if length is not None:
        headers["Content-Length"] = length
    assert a.post(PATH, content=content, headers=headers).status_code == status
    assert gateway.input_calls == []


def test_upload_quota_charges_original_bytes_before_transfer(clients):
    a, _, gateway, factory = clients
    csrf = register(a, "upload-quota@example.test")
    uploading(gateway, factory)
    a.app.state.input_limits = Limits(user_bytes=len(gateway.image) + 256 * 1024 - 1)
    assert a.post(PATH, content=gateway.image, headers={"Content-Type": "image/png", "X-CSRF-TOKEN": csrf}).status_code == 409
    assert gateway.input_calls == []


@pytest.mark.parametrize("confirmed", [False, True])
def test_unknown_upload_retains_known_identity_and_quota_until_confirmed_cleanup(clients, confirmed):
    a, _, gateway, factory = clients
    csrf = register(a, "upload-unknown@example.test")
    uploading(gateway, factory)
    async def upload(*args):
        gateway.input_calls.append(("upload", args[0]))
        raise GatewayError("unavailable")
    async def remove(key):
        gateway.input_calls.append(("delete", key))
        if not confirmed:
            raise GatewayError("unavailable")
        return {"deleted": True}
    gateway.upload_input, gateway.delete_input = upload, remove
    assert a.post(PATH, content=gateway.image, headers={"Content-Type": "image/png", "X-CSRF-TOKEN": csrf}).status_code == 503
    with factory() as db:
        row = db.scalar(select(ManagedInput))
        assert row.state == ("revoked" if confirmed else "create_unknown")
        assert row.upstream_input_id is not None and row.accounted_bytes >= len(gateway.image)
        assert gateway.input_calls == [("upload", row.upstream_input_id), ("delete", row.upstream_input_id)]


def test_upload_receipt_identity_mismatch_is_not_published(clients):
    a, _, gateway, factory = clients
    csrf = register(a, "upload-mismatch@example.test")
    real = uploading(gateway, factory)
    async def upload(*args):
        raw = await real(*args)
        return {**raw, "input_id": uuid.uuid4().hex}
    gateway.upload_input = upload
    assert a.post(PATH, content=gateway.image, headers={"Content-Type": "image/png", "X-CSRF-TOKEN": csrf}).status_code == 422
    with factory() as db:
        assert db.scalar(select(ManagedInput)).state == "revoked"


def test_retired_upload_selection_preserves_input_and_historical_deletion_gate(clients):
    a, _, gateway, factory = clients
    csrf = register(a, "upload-retired@example.test")
    uploading(gateway, factory)
    value = a.post(PATH, content=gateway.image, headers={"Content-Type": "image/png", "X-CSRF-TOKEN": csrf}).json()
    body = {**image_request(), "workflowId": "retired-reference", "workflowKind": "definition",
            "definitionVersion": 7, "definitionDigest": "sha256:" + "a" * 64,
            "referenceInputId": value["id"]}
    assert a.post("/api/generation/image/jobs", json=body, headers={"X-CSRF-TOKEN": csrf}).status_code == 422
    assert gateway.submit_count == 0
    assert a.get("/api/generation/inputs/" + value["id"]).json()["available"]
    with factory() as db:
        row = db.get(ManagedInput, uuid.UUID(value["id"]))
        db.add(Execution(user_id=row.user_id, workflow="retired-reference",
                         reference_input_id=row.id, request_snapshot=body,
                         last_known_status="submission_unknown"))
        db.commit()
    assert a.delete("/api/generation/inputs/" + value["id"], headers={"X-CSRF-TOKEN": csrf}).status_code == 409


def test_session_revocation_during_upload_prevents_returning_private_handle(clients):
    a, _, gateway, factory = clients
    csrf = register(a, "upload-revoked@example.test")
    real = uploading(gateway, factory)
    async def upload(*args):
        raw = await real(*args)
        with factory() as db:
            db.execute(delete(LoginSession)); db.commit()
        return raw
    gateway.upload_input = upload
    response = a.post(PATH, content=gateway.image, headers={"Content-Type": "image/png", "X-CSRF-TOKEN": csrf})
    assert response.status_code == 401 and "thumbnailUrl" not in response.text
