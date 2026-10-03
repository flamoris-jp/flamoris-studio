"""Exercise downloads above the inline limit without allocating a whole asset."""

import asyncio
import base64
import hashlib
import uuid

import pytest
from fastapi import Request
from fastapi.responses import StreamingResponse
from starlette.requests import ClientDisconnect
from test_studio import clients as studio_clients
from test_studio import image_request, register

from flamoris_studio.app import create_app
from flamoris_studio.auth import COOKIE, authenticated_user
from flamoris_studio.gateway import GatewayError
from flamoris_studio.media import Thumbnails
from flamoris_studio.range_transfer import representation_response
from flamoris_studio.transfer import CHUNK_BYTES

clients = studio_clients
LARGE_BYTES = 65 * 1024 * 1024 + 37
TRANSFER_CAP = 80 * 1024 * 1024
UPSTREAM_ID = "large-generated-asset"


def generated_part(offset, length):
    """Make only the requested chunk; block-specific bytes detect cursor mistakes."""
    seed = hashlib.sha256(str(offset // CHUNK_BYTES).encode("ascii")).digest()
    repeats, tail = divmod(length, len(seed))
    return seed * repeats + seed[:tail]


def generated_digest():
    checksum = hashlib.sha256()
    for offset in range(0, LARGE_BYTES, CHUNK_BYTES):
        checksum.update(generated_part(offset, min(CHUNK_BYTES, LARGE_BYTES - offset)))
    return checksum.hexdigest()


class GeneratedGateway:
    """An immutable receipt plus on-demand chunks; no whole-file bytes are stored."""

    def __init__(self, mime="image/png", *, corrupt_digest=False):
        self.mime = mime
        self.digest = generated_digest()
        self.receipt_digest = "0" * 64 if corrupt_digest else self.digest
        self.prepares = self.reads = self.next_offset = self.max_chunk = 0

    async def assets(self, _job):
        return [
            {
                "asset_id": UPSTREAM_ID,
                "filename": "../../large.png"
                if self.mime == "image/png"
                else "large.wav",
                "mime_type": self.mime,
                "media_kind": "image" if self.mime == "image/png" else "audio",
                "size_bytes": LARGE_BYTES,
                "output_index": 0,
            }
        ]

    async def content(self, *_args, **_kwargs):
        raise AssertionError("Large downloads must not use whole-blob content")

    async def result(self, *_args, **_kwargs):
        raise AssertionError("The catalog must not materialize a result payload")

    async def prepare_asset(self, asset):
        assert asset == UPSTREAM_ID
        self.prepares += 1
        return {
            "asset_id": asset,
            "mime_type": self.mime,
            "size_bytes": LARGE_BYTES,
            "sha256": self.receipt_digest,
            "transfer_version": 1,
            "chunk_bytes": CHUNK_BYTES,
        }

    async def read_asset(self, asset, digest, offset, length):
        assert asset == UPSTREAM_ID and digest == self.receipt_digest
        assert offset == self.next_offset
        assert 0 < length <= CHUNK_BYTES
        data = generated_part(offset, min(length, LARGE_BYTES - offset))
        self.reads += 1
        self.max_chunk = max(self.max_chunk, len(data))
        self.next_offset += len(data)
        return {
            "asset_id": asset,
            "sha256": digest,
            "offset": offset,
            "size_bytes": LARGE_BYTES,
            "data_base64": base64.b64encode(data).decode("ascii"),
            "chunk_sha256": hashlib.sha256(data).hexdigest(),
            "next_offset": self.next_offset,
            "eof": self.next_offset == LARGE_BYTES,
        }


def catalog_asset(clients, source):
    owner, other, gateway, _factory = clients
    csrf = register(owner, "large-stream-owner@example.test")
    register(other, "large-stream-other@example.test")
    for name in ("assets", "prepare_asset", "read_asset", "content", "result"):
        setattr(gateway, name, getattr(source, name))
    created = owner.post(
        "/api/generation/image/jobs",
        json=image_request(),
        headers={"X-CSRF-TOKEN": csrf},
    )
    assert created.status_code == 201, created.text
    result = owner.get(f"/api/executions/{created.json()['id']}/result")
    assert result.status_code == 200, result.text
    asset = result.json()["assets"][0]
    assert asset["sizeBytes"] == LARGE_BYTES
    assert source.prepares == source.reads == 0

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("Owner denial must precede every upstream call")

    original = {
        name: getattr(gateway, name)
        for name in (
            "discover",
            "build",
            "submit",
            "status",
            "result",
            "cancel",
            "assets",
            "delete_asset",
            "content",
            "prepare_asset",
            "read_asset",
        )
    }
    for name in original:
        setattr(gateway, name, forbidden)
    try:
        assert other.get(asset["downloadUrl"]).status_code == 404
        other.cookies.clear()
        assert other.get(asset["downloadUrl"]).status_code == 401
    finally:
        for name, method in original.items():
            setattr(gateway, name, method)
    assert source.prepares == source.reads == 0
    return asset


async def connected_receive():
    return {"type": "http.request", "body": b"", "more_body": False}


def download_endpoint(app):
    return next(
        route.endpoint
        for route in app.routes
        if getattr(route, "path", None)
        == "/api/executions/{execution_id}/assets/{asset_id}/download"
    )


def test_download_fixture_finds_production_route_without_postgres(tmp_path):
    app = create_app(
        session_factory=object(), gateway=object(), thumbnails=Thumbnails(str(tmp_path))
    )
    # Recent FastAPI also keeps included router objects without a .path here.
    assert download_endpoint(app).__name__ == "asset_content"


async def download_response(owner, db, asset):
    # TestClient buffers streamed bodies. Call the registered route with its real
    # cookie/DB authentication instead, so the consumer can inspect each yield.
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("testserver", 80),
            "path": asset["downloadUrl"],
            "query_string": b"",
            "app": owner.app,
            "headers": [
                (b"cookie", f"{COOKIE}={owner.cookies.get(COOKIE)}".encode("ascii"))
            ],
        },
        receive=connected_receive,
    )
    user_id = authenticated_user(request, db)
    endpoint = download_endpoint(owner.app)
    execution_id = uuid.UUID(asset["downloadUrl"].split("/")[3])
    return await endpoint(execution_id, uuid.UUID(asset["id"]), request, db, user_id)


async def consume_incrementally(response, source):
    checksum = hashlib.sha256()
    count = chunks = maximum = 0
    async for data in response.body_iterator:
        assert isinstance(data, bytes) and 0 < len(data) <= CHUNK_BYTES
        chunks += 1
        # One eager first read, then one read per yield; no full-file prefetch.
        assert source.reads == chunks
        count += len(data)
        maximum = max(maximum, len(data))
        checksum.update(data)
    return count, checksum.hexdigest(), maximum, chunks


@pytest.mark.asyncio
@pytest.mark.parametrize("mime", ["image/png", "audio/wav"])
async def test_authenticated_download_over_64_mib_is_incremental(
    clients, monkeypatch, mime
):
    monkeypatch.setenv("STUDIO_MAX_TRANSFER_BYTES", str(TRANSFER_CAP))
    owner, _other, _gateway, factory = clients
    source = GeneratedGateway(mime)
    asset = catalog_asset(clients, source)
    slots = owner.app.state.download_slots = asyncio.Semaphore(1)
    with factory() as db:
        response = await download_response(owner, db, asset)
        assert isinstance(response, StreamingResponse)
        assert response.status_code == 200 and not hasattr(response, "body")
        assert response.headers["Content-Length"] == str(LARGE_BYTES)
        assert response.headers["Content-Type"] == mime
        assert response.headers["Content-Disposition"].startswith("attachment;")
        assert "no-store" in response.headers["Cache-Control"]
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert slots.locked() and source.prepares == source.reads == 1
        count, digest, maximum, chunks = await consume_incrementally(response, source)
    assert count == LARGE_BYTES > 64 * 1024 * 1024
    assert digest == source.digest and maximum == source.max_chunk == CHUNK_BYTES
    assert chunks == (LARGE_BYTES + CHUNK_BYTES - 1) // CHUNK_BYTES
    assert source.next_offset == LARGE_BYTES and not slots.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("mime", ["image/png", "audio/wav"])
async def test_large_download_with_bad_whole_digest_withholds_last_chunk(
    clients, monkeypatch, mime
):
    monkeypatch.setenv("STUDIO_MAX_TRANSFER_BYTES", str(TRANSFER_CAP))
    owner, _other, _gateway, factory = clients
    source = GeneratedGateway(mime, corrupt_digest=True)
    asset = catalog_asset(clients, source)
    slots = owner.app.state.download_slots = asyncio.Semaphore(1)
    received = 0
    with factory() as db:
        response = await download_response(owner, db, asset)
        with pytest.raises(GatewayError) as error:
            async for data in response.body_iterator:
                assert len(data) <= CHUNK_BYTES
                received += len(data)
    assert error.value.code == "validation"
    assert received == LARGE_BYTES - 37
    assert source.next_offset == LARGE_BYTES and not slots.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("mime", ["image/png", "audio/wav"])
async def test_large_download_consumer_close_releases_transfer_slot(
    clients, monkeypatch, mime
):
    monkeypatch.setenv("STUDIO_MAX_TRANSFER_BYTES", str(TRANSFER_CAP))
    owner, _other, _gateway, factory = clients
    source = GeneratedGateway(mime)
    asset = catalog_asset(clients, source)
    slots = owner.app.state.download_slots = asyncio.Semaphore(1)
    with factory() as db:
        response = await download_response(owner, db, asset)
        assert len(await anext(response.body_iterator)) == CHUNK_BYTES
        assert slots.locked() and source.reads == 1
        await response.body_iterator.aclose()
    assert source.next_offset == CHUNK_BYTES and not slots.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("mime", ["image/png", "audio/wav"])
@pytest.mark.parametrize("fail_at", ["http.response.start", "http.response.body"])
async def test_large_download_asgi_disconnect_releases_transfer_slot(
    clients, monkeypatch, mime, fail_at
):
    monkeypatch.setenv("STUDIO_MAX_TRANSFER_BYTES", str(TRANSFER_CAP))
    owner, _other, _gateway, factory = clients
    source = GeneratedGateway(mime)
    asset = catalog_asset(clients, source)
    slots = owner.app.state.download_slots = asyncio.Semaphore(1)
    messages = []

    async def send(message):
        messages.append(message["type"])
        if message["type"] == fail_at:
            raise OSError("Browser disconnected during response send")

    with factory() as db:
        response = await download_response(owner, db, asset)
        assert slots.locked() and source.reads == 1
        with pytest.raises(ClientDisconnect):
            await response(
                {"type": "http", "asgi": {"spec_version": "2.4"}},
                connected_receive,
                send,
            )
        assert messages == (
            ["http.response.start"]
            if fail_at == "http.response.start"
            else ["http.response.start", "http.response.body"]
        )
        assert not slots.locked() and source.next_offset == CHUNK_BYTES
        assert not owner.app.state.prepare_slots.locked()
        # Closing the iterator as well must not over-release the same slot.
        await response.body_iterator.aclose()
        await asyncio.wait_for(slots.acquire(), timeout=0.1)
        assert slots.locked()
        slots.release()


@pytest.mark.asyncio
async def test_generated_large_stream_runs_without_postgres_or_whole_blob():
    # The same bounded generator also runs locally when the PG route fixtures skip.
    source = GeneratedGateway("audio/wav")
    slots = asyncio.Semaphore(1)
    checks = 0

    async def authorize():
        nonlocal checks
        checks += 1

    response = await representation_response(
        gateway=source,
        prepare=lambda: source.prepare_asset(UPSTREAM_ID),
        authorize=authorize,
        slots=slots,
        upstream_id=UPSTREAM_ID,
        mime=source.mime,
        display_name="large.wav",
        max_bytes=TRANSFER_CAP,
        attachment=True,
    )
    assert source.reads == 1 and slots.locked()
    count, digest, maximum, chunks = await consume_incrementally(response, source)
    assert count == LARGE_BYTES > 64 * 1024 * 1024
    assert digest == source.digest and maximum == CHUNK_BYTES
    assert checks == 3 * chunks and not slots.locked()
