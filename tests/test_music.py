import copy
import importlib.util
import time
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, select, text

from flamoris_studio.app import create_app
from flamoris_studio.db import Asset, Execution, GenerationRequestRecord, LoginSession, ManagedInput
from flamoris_studio.gateway import GatewayError, GenerationGateway
from flamoris_studio.music_contract import MusicRequest, TranscriptionRequest, checked_music_build, checked_music_outputs, contract, qualified_music
from flamoris_studio.result_contract import normalize_outputs
from test_studio import clients, register
from test_speech import setup_speech, REQUEST

JOB = "b" * 32
CAPABILITIES = {"capabilities": [{"id": "music." + operation, "provider_id": contract(operation)["provider_id"], "available": True, "workflow_templates": [contract(operation)["id"]]} for operation in ("generate", "transcribe")]}


def listing(operation="generate", score=True):
    formats = {"audio": ("audio", "audio/wav", ".wav"), "score": ("score", "text/vnd.abc", ".abc"), "metadata": ("metadata", "application/json", ".json"), "midi": ("midi", "audio/midi", ".mid"), "events": ("metadata", "application/json", ".json"), "summary": ("metadata", "application/json", ".json")}
    roles = ["audio", "score", "metadata"] if operation == "generate" else ["midi", "events", "summary"] + (["score"] if score else [])
    return [{"asset_id": role + "-upstream", "filename": role + formats[role][2], "mime_type": formats[role][1], "media_kind": formats[role][0], "size_bytes": 10, "port": role, "role": role, "role_index": 0} for role in roles]


def test_catalog_is_pinned_and_health_alone_cannot_enable_music():
    catalog = {"descriptors": [contract("generate"), contract("transcribe")]}
    for operation in ("generate", "transcribe"):
        assert qualified_music(catalog, CAPABILITIES, operation)
        for key, value in [("schema_version", True), ("provider_id", "other"), ("version", 2), ("readiness", {"status": "ready"}), ("qualification", "stub")]:
            altered = copy.deepcopy(contract(operation)); altered[key] = value
            assert not qualified_music({"descriptors": [altered]}, CAPABILITIES, operation)
        assert not qualified_music({"descriptors": [contract(operation)] * 2}, CAPABILITIES, operation)
        altered = copy.deepcopy(contract(operation)); altered["parameters"]["unreviewed"] = {"type": "string"}
        assert not qualified_music({"descriptors": [altered]}, CAPABILITIES, operation)


@pytest.mark.parametrize("values", [{"style": " "}, {"style": "x\x00"}, {"style": "x" * 1025}, {"style": "ok", "lyrics": "x" * 4097}, {"style": "ok", "seconds": float("inf")}, {"style": "ok", "steps": True}, {"style": "ok", "seed": 2**53}, {"style": "ok", "lm_seed": True}, {"style": "ok", "abc": "unreviewed"}])
def test_music_parameter_bounds_fail_closed(values):
    with pytest.raises(ValidationError):
        MusicRequest.model_validate(values)


def test_pinned_build_and_required_output_manifest_and_abc_attachment():
    for operation, parameters in [("generate", MusicRequest(style="calypso", seed=0).model_dump()), ("transcribe", {"audio": "d" * 32, "max_seconds": 30.0, "melody_only": False})]:
        descriptor = contract(operation); proof = "music" if operation == "generate" else "transcription"
        build = {"schema_version": descriptor["schema_version"], "template": descriptor["id"], "workflow_id": "a" * 32, "parameters": parameters, "prompt": None, proof: descriptor[proof]}
        assert checked_music_build(build, parameters, operation) == "a" * 32
        for key, value in [("schema_version", True), ("prompt", {}), (proof, {}), ("parameters", {**parameters, "other": True})]:
            with pytest.raises(GatewayError): checked_music_build({**build, key: value}, parameters, operation)
        outputs = normalize_outputs(listing(operation)); checked_music_outputs(outputs, "music." + operation)
        score = next(x for x in outputs if x.mime_type == "text/vnd.abc")
        assert score.media_kind == "score" and score.role["role"] == "score"
        with pytest.raises(GatewayError): checked_music_outputs(outputs[1:], "music." + operation)
    no_score = normalize_outputs(listing("transcribe", score=False))
    checked_music_outputs(no_score, "music.transcribe")
    with pytest.raises(GatewayError): checked_music_outputs(no_score, "music.transcribe", {"melody_only": True})
    for missing in ("midi", "events", "summary"):
        with pytest.raises(GatewayError): checked_music_outputs([x for x in no_score if x.role["role"] != missing], "music.transcribe")


@pytest.mark.asyncio
async def test_independent_optin_requires_exact_catalog_and_managed_wav_infrastructure(monkeypatch):
    calls = []; health = {"healthy": True, "managed_input_support": {"ready": True}}
    async def result(name, arguments=None):
        calls.append(name)
        return {"system.health": health, "workflows.list": {"descriptors": [contract("generate"), contract("transcribe")]}, "capabilities.list": CAPABILITIES}[name]
    gateway = GenerationGateway(); monkeypatch.setattr(gateway, "_json", result)
    monkeypatch.delenv("STUDIO_MUSIC_ENABLED", raising=False)
    assert await gateway.discover_music() == {"generate": False, "transcribe": False} and not calls
    monkeypatch.setenv("STUDIO_MUSIC_ENABLED", "true")
    assert await gateway.discover_music() == {"generate": True, "transcribe": True}
    health.pop("managed_input_support")
    assert await gateway.discover_music() == {"generate": True, "transcribe": False}


def test_managed_audio_metadata_cannot_change_its_source_mime():
    from types import SimpleNamespace
    from flamoris_studio.managed_inputs import validate_metadata
    row = SimpleNamespace(source_upstream_id="source", mime_type="audio/wav")
    raw = {"input_id": "d" * 32, "source_asset_id": "source", "mime_type": "audio/wav", "media_kind": "audio", "size_bytes": 10, "sha256": "e" * 64, "expires_at": time.time() + 80000}
    assert validate_metadata(raw, row, creating=True) == raw
    with pytest.raises(GatewayError):
        validate_metadata({**raw, "mime_type": "image/png", "media_kind": "image"}, row, creating=True)


def setup_music(clients, monkeypatch):
    a, b, gateway, factory = clients
    ca, cb = register(a, "music-a@example.test"), register(b, "music-b@example.test")
    monkeypatch.setenv("STUDIO_MUSIC_ENABLED", "true")
    builds, submissions = [], []
    async def discover(): return {"generate": True, "transcribe": True}
    async def build(parameters, operation): builds.append((parameters, operation)); return "a" * 32
    async def submit(workflow): submissions.append(workflow); return {"job_id": JOB, "status": "queued"}
    async def assets(job): return listing(builds[-1][1])
    gateway.discover_music, gateway.build_music, gateway.submit, gateway.assets = discover, build, submit, assets
    return a, b, gateway, factory, ca, cb, builds, submissions


def test_owned_generate_and_transcription_use_shared_results_and_input_lifecycle(clients, monkeypatch):
    a, b, gateway, factory, ca, cb, builds, submissions = setup_music(clients, monkeypatch)
    made = a.post("/api/generation/music/generate/jobs", json={"style": "calypso", "requestId": REQUEST}, headers={"X-CSRF-TOKEN": ca})
    assert made.status_code == 201, made.text
    result = a.get(f"/api/executions/{made.json()['id']}/result").json()
    assert result["category"] == "music" and result["operation"] == "music.generate"
    audio = next(x for x in result["assets"] if x["mimeType"] == "audio/wav")
    score = next(x for x in result["assets"] if x["mimeType"] == "text/vnd.abc")
    assert score["displayName"].endswith(".abc") and score["previewKind"] == "file"
    assert b.get(f"/api/executions/{made.json()['id']}/result").status_code == 404
    assert b.get(score["downloadUrl"]).status_code == 404
    calls = []
    record = {"input_id": "d" * 32, "source_asset_id": "audio-upstream", "mime_type": "audio/wav", "media_kind": "audio", "size_bytes": 10, "sha256": "e" * 64, "expires_at": time.time() + 80000}
    async def create(asset): calls.append(asset); return record
    async def get(key): return record
    async def delete_input(key): return {"deleted": True}
    gateway.create_input, gateway.get_input, gateway.delete_input = create, get, delete_input
    assert b.post("/api/generation/inputs", json={"assetId": audio["id"]}, headers={"X-CSRF-TOKEN": cb}).status_code == 404 and not calls
    ref = a.post("/api/generation/inputs", json={"assetId": audio["id"]}, headers={"X-CSRF-TOKEN": ca})
    assert ref.status_code == 201 and ref.json()["thumbnailUrl"] is None
    with factory() as db:
        row = db.get(ManagedInput, uuid.UUID(ref.json()["id"]))
        assert row.accounted_bytes == 0 and row.mime_type == "audio/wav"
    body = {"referenceInputId": ref.json()["id"], "requestId": str(uuid.uuid4())}
    assert b.post("/api/generation/music/transcribe/jobs", json=body, headers={"X-CSRF-TOKEN": cb}).status_code == 404
    transcription = a.post("/api/generation/music/transcribe/jobs", json=body, headers={"X-CSRF-TOKEN": ca})
    assert transcription.status_code == 201, transcription.text
    assert builds[-1] == ({"audio": "d" * 32, "max_seconds": 30.0, "melody_only": False}, "transcribe")
    assert a.delete(f"/api/generation/inputs/{ref.json()['id']}", headers={"X-CSRF-TOKEN": ca}).status_code == 409
    async def partial(job): return listing("transcribe", score=False)
    gateway.assets = partial
    result = a.get(f"/api/executions/{transcription.json()['id']}/result").json()
    assert result["warnings"] == ["abc_unavailable"] and len(result["assets"]) == 3
    assert a.delete(f"/api/generation/inputs/{ref.json()['id']}", headers={"X-CSRF-TOKEN": ca}).status_code == 200


def test_unknown_receipt_fence_seed_and_account_isolation_survive_restart(clients, monkeypatch):
    a, b, gateway, factory, ca, cb, builds, submissions = setup_music(clients, monkeypatch)
    monkeypatch.setattr("flamoris_studio.music.secrets.randbelow", lambda maximum: 456)
    async def unknown(workflow): submissions.append(workflow); raise GatewayError("unavailable")
    gateway.submit = unknown
    body = {"style": "calypso", "seed": None, "lm_seed": None, "requestId": REQUEST}
    response = a.post("/api/generation/music/generate/jobs", json=body, headers={"X-CSRF-TOKEN": ca})
    assert response.status_code == 201 and response.json()["state"] == "submission_unknown"
    assert builds[0][0]["seed"] == builds[0][0]["lm_seed"] == 456
    with TestClient(create_app(factory, gateway, a.app.state.thumbnails)) as restarted:
        restarted.cookies.update(a.cookies)
        assert restarted.post("/api/generation/music/generate/jobs", json=body, headers={"X-CSRF-TOKEN": ca}).json() == response.json()
        assert restarted.get(f"/api/generation/music/requests/{REQUEST}").json() == response.json()
    assert len(submissions) == len(builds) == 1
    assert b.get(f"/api/generation/music/requests/{REQUEST}").status_code == 404
    assert b.post("/api/generation/music/generate/jobs", json=body, headers={"X-CSRF-TOKEN": cb}).status_code == 201
    assert len(submissions) == 2
    changed = a.post("/api/generation/music/generate/jobs", json={**body, "style": "other"}, headers={"X-CSRF-TOKEN": ca})
    assert changed.status_code == 409


def test_speech_request_migration_preserves_replay_and_rejects_cross_operation_uuid(clients, monkeypatch):
    a, b, gateway, factory, ca, builds, submissions = setup_speech(clients, monkeypatch)
    body = {"text": "hello", "requestId": REQUEST}
    response = a.post("/api/generation/speech/jobs", json=body, headers={"X-CSRF-TOKEN": ca})
    assert response.status_code == 201
    with factory() as db:
        db.execute(text("ALTER TABLE generation_requests RENAME TO speech_requests")); db.commit()
        module_path = Path(__file__).parents[1] / "alembic/versions/20261003_10_generation_requests.py"
        spec = importlib.util.spec_from_file_location("request_migration", module_path); migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        migration.op = Operations(MigrationContext.configure(db.connection()))
        migration.upgrade(); db.commit()
        assert db.get(GenerationRequestRecord, (db.scalar(select(Execution.user_id)), uuid.UUID(REQUEST))) is not None
    assert a.post("/api/generation/speech/jobs", json=body, headers={"X-CSRF-TOKEN": ca}).json() == response.json()
    assert len(submissions) == 1
    monkeypatch.setenv("STUDIO_MUSIC_ENABLED", "true")
    assert a.post("/api/generation/music/generate/jobs", json={"style": "calypso", "requestId": REQUEST}, headers={"X-CSRF-TOKEN": ca}).status_code == 409
    assert a.get(f"/api/generation/music/requests/{REQUEST}").status_code == 404


def test_changed_configuration_or_revoked_auth_after_build_cannot_submit(clients, monkeypatch):
    a, _, gateway, factory, ca, _, builds, submissions = setup_music(clients, monkeypatch)
    async def changed(parameters, operation): monkeypatch.setenv("STUDIO_MUSIC_ENABLED", "false"); return "a" * 32
    gateway.build_music = changed
    assert a.post("/api/generation/music/generate/jobs", json={"style": "calypso", "requestId": REQUEST}, headers={"X-CSRF-TOKEN": ca}).status_code == 409
    assert not submissions
    monkeypatch.setenv("STUDIO_MUSIC_ENABLED", "true")
    async def revoked(parameters, operation):
        with factory() as db: db.execute(delete(LoginSession)); db.commit()
        return "a" * 32
    gateway.build_music = revoked
    assert a.post("/api/generation/music/generate/jobs", json={"style": "calypso", "requestId": str(uuid.uuid4())}, headers={"X-CSRF-TOKEN": ca}).status_code == 401
    assert not submissions
