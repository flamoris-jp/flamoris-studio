import base64
import hashlib
import uuid

from sqlalchemy import select
from test_studio import clients, register

from flamoris_studio.db import Asset, Execution, User


def test_music_wav_cards_filters_owned_attachments_and_orphan_cleanup(clients):
    a, b, gateway, factory = clients
    csrf = register(a, "gallery-owner@example.test")
    register(b, "gallery-other@example.test")
    metadata, binaries = {}, {}
    with factory() as db:
        owner = db.scalar(select(User).where(User.email == "gallery-owner@example.test")).id
        foreign = db.scalar(select(User).where(User.email == "gallery-other@example.test")).id
        def create(category, operation, outputs, user=owner):
            execution = Execution(user_id=user, category=category, operation=operation,
                workflow="fixture", upstream_job_id=uuid.uuid4().hex, last_known_status="completed")
            db.add(execution); db.flush()
            result = []
            for kind, mime in outputs:
                handle, upstream = uuid.uuid4(), uuid.uuid4().hex + ":000"
                data = b"bounded test attachment"
                row = Asset(id=handle, user_id=user, execution_id=execution.id,
                    upstream_asset_id=upstream, storage_locator=upstream, media_kind=kind,
                    mime_type=mime, display_name=str(handle), original_filename="fixture", size_bytes=len(data))
                db.add(row); result.append(str(handle))
                binaries[upstream] = data
                metadata[upstream] = {"asset_id": upstream, "mime_type": mime, "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(), "transfer_version": 1, "chunk_bytes": 262144}
            return result
        music = create("music", "music.generate", [("audio", "audio/wav"), ("score", "text/vnd.abc"),
                      ("midi", "audio/midi"), ("metadata", "application/json")])
        # Historical external imports used 'multimodal' rather than 'music'.
        legacy = create("multimodal", "external.import", [("audio", "audio/wav"), ("score", "text/vnd.abc")])
        image = create("image", "image.generate", [("image", "image/png")])
        speech = create("speech", "speech.generate", [("audio", "audio/wav")])
        other = create("music", "music.transcribe", [("midi", "audio/midi")])
        hidden = create("music", "music.generate", [("audio", "audio/wav"), ("score", "text/vnd.abc")], foreign)
        db.commit()
    async def prepare(identifier):
        return metadata[identifier]
    async def read(identifier, digest, offset, length):
        data = binaries[identifier][offset:offset + length]
        return {"asset_id": identifier, "sha256": digest, "offset": offset,
                "size_bytes": len(binaries[identifier]), "data_base64": base64.b64encode(data).decode(),
                "chunk_sha256": hashlib.sha256(data).hexdigest(), "next_offset": offset + len(data),
                "eof": offset + len(data) == len(binaries[identifier])}
    gateway.prepare_asset, gateway.read_asset = prepare, read
    for kind, handles in (("music", music[:1] + legacy[:1]), ("image", image), ("speech", speech), ("other", other)):
        page = a.get("/api/assets", params={"kind": kind}).json()
        assert {row["id"] for row in page["items"]} == set(handles)
        assert all(row["category"] == kind for row in page["items"])
    first = a.get("/api/assets?kind=music&limit=1").json()
    second = a.get(f"/api/assets?kind=music&limit=1&offset={first['nextOffset']}").json()
    assert first["nextOffset"] == 1 and second["nextOffset"] is None
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert len(a.get("/api/assets").json()["items"]) == 5
    assert a.get("/api/assets?kind=unknown").status_code == 422
    detail = a.get(f"/api/assets/{music[0]}").json()
    assert {row["id"] for row in detail["attachments"]} == set(music[1:])
    for attachment in detail["attachments"]:
        assert a.get(attachment["downloadUrl"]).content == b"bounded test attachment"
        assert b.get(attachment["downloadUrl"]).status_code == 404
        assert b.get(f"/api/assets/{attachment['id']}").status_code == 404
    assert a.get(f"/api/assets/{hidden[0]}").status_code == 404
    # File deletion remains explicit: surviving sidecars become Other files,
    # rather than inaccessible hidden debris after WAV deletion.
    assert a.post("/api/assets/delete", json={"ids": music[:1]}, headers={"X-CSRF-TOKEN": csrf}).json()["results"][0]["deleted"]
    assert set(music[1:]).issubset({row["id"] for row in a.get("/api/assets?kind=other").json()["items"]})


def test_attachment_queries_do_not_mix_owners_even_with_inconsistent_rows(clients):
    a, b, _, factory = clients
    register(a, "consistent@example.test")
    register(b, "foreign-row@example.test")
    with factory() as db:
        owner, foreign = [db.scalar(select(User).where(User.email == name)).id for name in
                          ("consistent@example.test", "foreign-row@example.test")]
        execution = Execution(user_id=owner, category="music", operation="music.generate", workflow="fixture")
        db.add(execution); db.flush()
        main = Asset(user_id=owner, execution_id=execution.id, upstream_asset_id="private-main",
            storage_locator="private-main", display_name="main.wav", original_filename="main.wav",
            media_kind="audio", mime_type="audio/wav")
        db.add(main)
        db.add(Asset(user_id=foreign, execution_id=execution.id, upstream_asset_id="private-foreign",
            storage_locator="private-foreign", display_name="foreign.abc", original_filename="foreign.abc",
            media_kind="score", mime_type="text/vnd.abc"))
        db.commit(); identifier = main.id
    assert a.get(f"/api/assets/{identifier}").json()["attachments"] == []
    assert b.get(f"/api/assets/{identifier}").status_code == 404
