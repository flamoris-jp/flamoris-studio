import base64
import hashlib
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select
from test_studio import clients as studio_clients
from test_studio import image_request, register

from flamoris_studio.db import Asset, Execution, LoginSession, User
from flamoris_studio.gateway import GatewayError
from flamoris_studio.range_transfer import representation_response, single_range
from flamoris_studio.result_contract import normalize_outputs, preview_kind

clients = studio_clients


@pytest.mark.parametrize(
    "header,result",
    [
        ("bytes=0-0", (0, 0)),
        ("bytes=2-5", (2, 5)),
        ("bytes=7-", (7, 9)),
        ("bytes=-3", (7, 9)),
        ("bytes=0-100", (0, 9)),
        ("bytes=-100", (0, 9)),
    ],
)
def test_valid_single_ranges(header, result):
    assert single_range(header, 10) == result


@pytest.mark.parametrize(
    "header",
    [
        "bytes=",
        "bytes=-0",
        "bytes=10-",
        "bytes=5-3",
        "bytes=0-1,3-4",
        "items=0-1",
        "bytes=+1-2",
        "bytes=1.0-2",
        "bytes=-",
        "bytes=" + "9" * 130 + "-",
    ],
)
def test_unsupported_or_unsatisfiable_range_returns_416(header):
    with pytest.raises(HTTPException) as error:
        single_range(header, 10)
    assert error.value.status_code == 416
    assert error.value.headers == {"Content-Range": "bytes */10"}


def output(kind="audio", mime="audio/wav", **extra):
    return {
        "asset_id": "owned-upstream",
        "media_kind": kind,
        "mime_type": mime,
        "filename": "../../safe.wav",
        "size_bytes": 10,
        **extra,
    }


def test_roles_are_explicit_bounded_and_formats_do_not_come_from_filename():
    legacy = normalize_outputs([output()])[0]
    assert legacy.role is None and legacy.display_name == "safe.wav"
    role = normalize_outputs(
        [output(port="audio", role="primary_audio", role_index=0)]
    )[0].role
    assert role == {"port": "audio", "role": "primary_audio", "index": 0}
    unknown = normalize_outputs([output(kind="image", mime="text/html")])[0]
    assert (
        unknown.media_kind == "unknown"
        and preview_kind(unknown.media_kind, unknown.mime_type) == "file"
    )
    assert preview_kind("midi", "audio/midi") == "file"
    assert preview_kind("image", "image/vnd.adobe.photoshop") == "file"
    assert preview_kind("image", "audio/wav") == "file"


@pytest.mark.parametrize(
    "items",
    [
        [output(role="audio")],
        [output(port="audio", role="audio", role_index=True)],
        [
            output(port="audio", role="audio", role_index=0),
            output(asset_id="other", port="audio", role="audio", role_index=0),
        ],
        [
            output(port="audio", role="audio", role_index=0),
            output(asset_id="other", port="audio", role="other", role_index=1),
        ],
        [output(mime="audio/wav\r\nInjected: yes")],
        [output(), output()],
    ],
)
def test_invalid_complete_manifest_is_refused(items):
    with pytest.raises(GatewayError):
        normalize_outputs(items)


class BytesGateway:
    def __init__(self, mime="audio/wav", data=b"0123456789"):
        self.mime, self.data = mime, data
        self.digest = hashlib.sha256(data).hexdigest()
        self.reads = []
        self.prepares = 0

    async def prepare_asset(self, asset):
        self.prepares += 1
        return {
            "asset_id": asset,
            "mime_type": self.mime,
            "size_bytes": len(self.data),
            "sha256": self.digest,
            "transfer_version": 1,
            "chunk_bytes": 4,
        }

    async def read_asset(self, asset, digest, offset, length):
        self.reads.append((asset, digest, offset, length))
        part = self.data[offset : offset + length]
        return {
            "asset_id": asset,
            "sha256": digest,
            "offset": offset,
            "size_bytes": len(self.data),
            "data_base64": base64.b64encode(part).decode(),
            "chunk_sha256": hashlib.sha256(part).hexdigest(),
            "next_offset": offset + len(part),
            "eof": offset + len(part) == len(self.data),
        }


def install_bytes_gateway(gateway, byte_source, listing):
    async def assets(_):
        return listing

    gateway.assets = assets
    gateway.prepare_asset = byte_source.prepare_asset
    gateway.read_asset = byte_source.read_asset


def test_audio_catalog_range_download_ownership_and_tombstone(clients):
    a, b, gateway, factory = clients
    csrf = register(a, "media-owner@example.com")
    register(b, "media-other@example.com")
    byte_source = BytesGateway()
    install_bytes_gateway(
        gateway, byte_source, [output(port="audio", role="primary_audio", role_index=0)]
    )
    execution = a.post(
        "/api/image/generate", json=image_request(), headers={"X-CSRF-TOKEN": csrf}
    ).json()
    response = a.get(f"/api/executions/{execution['id']}/result").json()
    asset = response["assets"][0]
    assert (
        response["source"] == "generation" and response["operation"] == "image.generate"
    )
    assert asset["outputRole"] == {"port": "audio", "role": "primary_audio", "index": 0}
    assert asset["previewKind"] == "audio" and byte_source.reads == []
    assert a.get(asset["thumbnailUrl"]).status_code == 404
    for route in ("content", "download", "thumbnail"):
        url = f"/api/executions/{execution['id']}/assets/{asset['id']}/{route}"
        assert b.get(url, headers={"Range": "bytes=1-2"}).status_code == 404
    assert byte_source.prepares == 0
    ranged = a.get(asset["previewUrl"], headers={"Range": "bytes=2-5"})
    assert ranged.status_code == 206 and ranged.content == b"2345"
    assert ranged.headers["Content-Range"] == "bytes 2-5/10"
    assert (
        ranged.headers["Content-Length"] == "4"
        and ranged.headers["Accept-Ranges"] == "bytes"
    )
    assert byte_source.reads[-1][2:] == (2, 4)
    assert a.get(asset["previewUrl"], headers={"Range": "bytes=-3"}).content == b"789"
    assert (
        a.get(
            asset["previewUrl"], headers={"Range": "bytes=1-2", "If-Range": '"other"'}
        ).status_code
        == 200
    )
    invalid = a.get(asset["previewUrl"], headers={"Range": "bytes=50-60"})
    assert (
        invalid.status_code == 416 and invalid.headers["Content-Range"] == "bytes */10"
    )
    full = a.get(asset["downloadUrl"])
    assert full.content == byte_source.data and full.headers[
        "Content-Disposition"
    ].startswith("attachment;")
    assert (
        "no-store" in full.headers["Cache-Control"]
        and full.headers["X-Content-Type-Options"] == "nosniff"
    )
    assert a.post(
        "/api/assets/delete",
        json={"ids": [asset["id"]]},
        headers={"X-CSRF-TOKEN": csrf},
    ).json()["results"][0]["deleted"]
    assert a.get(asset["previewUrl"], headers={"Range": "bytes=1-2"}).status_code == 404
    assert a.get(f"/api/executions/{execution['id']}/result").json()["assets"] == []
    with factory() as db:
        assert (
            db.get(Asset, uuid.UUID(asset["id"])).extra_metadata["output_role"]["role"]
            == "primary_audio"
        )


@pytest.mark.parametrize(
    "kind,mime",
    [
        ("midi", "audio/midi"),
        ("metadata", "application/json"),
        ("image", "image/vnd.adobe.photoshop"),
        ("image", "text/html"),
    ],
)
def test_non_native_formats_remain_cataloged_and_attachment_only(clients, kind, mime):
    a, _, gateway, _ = clients
    csrf = register(a, "file-owner@example.com")
    source = BytesGateway(mime)
    install_bytes_gateway(gateway, source, [output(kind=kind, mime=mime)])
    made = a.post(
        "/api/image/generate", json=image_request(), headers={"X-CSRF-TOKEN": csrf}
    ).json()
    asset = a.get(f"/api/executions/{made['id']}/result").json()["assets"][0]
    assert asset["previewKind"] == "file" and source.prepares == 0
    response = a.get(asset["previewUrl"])
    assert response.content == source.data and response.headers[
        "Content-Disposition"
    ].startswith("attachment;")
    assert a.get(asset["thumbnailUrl"]).status_code == 404
    assert a.get("/api/assets").json()["items"][0]["id"] == asset["id"]


def test_role_conflict_does_not_mutate_durable_catalog_and_other_source_not_dispatched(
    clients,
):
    a, _, gateway, factory = clients
    csrf = register(a, "role-owner@example.com")
    source = BytesGateway()
    install_bytes_gateway(
        gateway, source, [output(port="audio", role="audio", role_index=0)]
    )
    made = a.post(
        "/api/image/generate", json=image_request(), headers={"X-CSRF-TOKEN": csrf}
    ).json()
    asset = a.get(f"/api/executions/{made['id']}/result").json()["assets"][0]
    install_bytes_gateway(
        gateway, source, [output(port="audio", role="other", role_index=0)]
    )
    assert a.get(f"/api/executions/{made['id']}/result").status_code == 422
    with factory() as db:
        assert (
            db.get(Asset, uuid.UUID(asset["id"])).extra_metadata["output_role"]["role"]
            == "audio"
        )
        execution = db.get(Execution, uuid.UUID(made["id"]))
        execution.source = "intelligence"
        db.commit()
    assert a.get(asset["downloadUrl"]).status_code == 404 and source.prepares == 0
    assert a.get(f"/api/executions/{made['id']}/result").status_code == 404
    assert (
        a.post(
            f"/api/executions/{made['id']}/cancel", headers={"X-CSRF-TOKEN": csrf}
        ).status_code
        == 404
    )


@pytest.mark.asyncio
async def test_oversized_receipt_preserves_download_limit_error_and_slot():
    import asyncio

    slots = asyncio.Semaphore(1)
    gateway = BytesGateway()

    async def authorize():
        pass

    with pytest.raises(GatewayError) as error:
        await representation_response(
            gateway=gateway,
            prepare=lambda: gateway.prepare_asset("a"),
            authorize=authorize,
            slots=slots,
            upstream_id="a",
            mime="audio/wav",
            display_name="a.wav",
            max_bytes=5,
        )
    assert error.value.code == "asset_too_large"
    assert not slots.locked() and not gateway.reads


@pytest.mark.asyncio
async def test_header_failure_releases_slot_even_before_stream_starts():
    import asyncio

    slots = asyncio.Semaphore(1)
    gateway = BytesGateway()

    async def authorize():
        pass

    response = await representation_response(
        gateway=gateway,
        prepare=lambda: gateway.prepare_asset("a"),
        authorize=authorize,
        slots=slots,
        upstream_id="a",
        mime="audio/wav",
        display_name="a.wav",
        max_bytes=100,
    )
    assert slots.locked()

    async def send(_):
        raise RuntimeError("header failure")

    async def receive():
        return {"type": "http.disconnect"}

    with pytest.raises(RuntimeError):
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    assert not slots.locked()


@pytest.mark.asyncio
async def test_range_chunk_integrity_authorization_loss_and_deadline_are_bounded(
    monkeypatch,
):
    import asyncio

    slots = asyncio.Semaphore(1)
    gateway = BytesGateway()
    checks = 0

    async def authorize():
        nonlocal checks
        checks += 1
        if checks > 1:
            raise HTTPException(401)

    with pytest.raises(HTTPException):
        await representation_response(
            gateway=gateway,
            prepare=lambda: gateway.prepare_asset("a"),
            authorize=authorize,
            slots=slots,
            upstream_id="a",
            mime="audio/wav",
            display_name="a.wav",
            max_bytes=100,
            allow_range=True,
            range_headers=["bytes=1-2"],
        )
    assert len(gateway.reads) == 1 and not slots.locked()

    monkeypatch.setattr("flamoris_studio.range_transfer.TRANSFER_SECONDS", 0)
    with pytest.raises(GatewayError):
        await representation_response(
            gateway=gateway,
            prepare=lambda: gateway.prepare_asset("a"),
            authorize=authorize,
            slots=slots,
            upstream_id="a",
            mime="audio/wav",
            display_name="a.wav",
            max_bytes=100,
        )
    assert not slots.locked()


@pytest.mark.asyncio
async def test_isolated_range_checks_chunks_and_full_read_checks_whole_digest():
    import asyncio

    slots = asyncio.Semaphore(1)
    gateway = BytesGateway()
    gateway.digest = (
        "a" * 64
    )  # A receipt is trusted; partial bytes cannot rehash the whole file.

    async def authorize():
        pass

    async def response(ranges):
        return await representation_response(
            gateway=gateway,
            prepare=lambda: gateway.prepare_asset("a"),
            authorize=authorize,
            slots=slots,
            upstream_id="a",
            mime="audio/wav",
            display_name="a.wav",
            max_bytes=100,
            allow_range=True,
            range_headers=ranges,
        )

    ranged = await response(["bytes=2-3"])
    assert b"".join([part async for part in ranged.body_iterator]) == b"23"
    assert not slots.locked()
    whole = await response([])
    with pytest.raises(GatewayError, match="Generation service error"):
        async for _ in whole.body_iterator:
            pass
    assert not slots.locked()


@pytest.mark.asyncio
async def test_corrupt_range_chunk_is_refused_before_response_and_not_retried():
    import asyncio

    slots = asyncio.Semaphore(1)
    gateway = BytesGateway()
    original = gateway.read_asset

    async def corrupt(*args):
        return {**await original(*args), "chunk_sha256": "a" * 64}

    gateway.read_asset = corrupt

    async def authorize():
        pass

    with pytest.raises(GatewayError):
        await representation_response(
            gateway=gateway,
            prepare=lambda: gateway.prepare_asset("a"),
            authorize=authorize,
            slots=slots,
            upstream_id="a",
            mime="audio/wav",
            display_name="a.wav",
            max_bytes=100,
            allow_range=True,
            range_headers=["bytes=2-3"],
        )
    assert len(gateway.reads) == 1 and not slots.locked()


def test_login_revoked_during_first_range_read_cannot_publish_media(clients):
    a, _, gateway, factory = clients
    csrf = register(a, "revoked-media@example.com")
    source = BytesGateway()
    install_bytes_gateway(gateway, source, [output()])
    made = a.post(
        "/api/image/generate", json=image_request(), headers={"X-CSRF-TOKEN": csrf}
    ).json()
    asset = a.get(f"/api/executions/{made['id']}/result").json()["assets"][0]
    with factory() as db:
        owner = db.scalar(
            select(User).where(User.email == "revoked-media@example.com")
        ).id
    original = source.read_asset

    async def revoke(*args):
        result = await original(*args)
        with factory() as db:
            db.execute(delete(LoginSession).where(LoginSession.user_id == owner))
            db.commit()
        return result

    gateway.read_asset = revoke
    response = a.get(asset["previewUrl"], headers={"Range": "bytes=0-1"})
    assert response.status_code == 401 and response.content != b"01"
    assert len(source.reads) == 1 and not a.app.state.download_slots.locked()
