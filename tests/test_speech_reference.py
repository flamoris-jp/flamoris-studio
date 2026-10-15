import copy
import hashlib
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from flamoris_generation_controller.speech import reference_speech_descriptor, speech_descriptor
from flamoris_studio.db import Execution, ManagedInput, now
from flamoris_studio.gateway import GatewayError, GenerationGateway
from flamoris_studio.speech_contract import (
    HTTP_PROOF, REFERENCE_PROOF, checked_reference_build, qualified_reference,
)
from test_input_uploads import uploading
from test_speech import REQUEST, setup_speech
from test_studio import clients


def reference_setup(clients, monkeypatch):
    a, b, gateway, factory, csrf, builds, submissions = setup_speech(clients, monkeypatch)
    original = uploading(gateway, factory)

    async def upload(key, data, mime):
        raw = await original(key, data, mime)
        raw["media_kind"] = mime.split("/")[0]
        return raw

    async def discover():
        return {"available": True, "speechContract": HTTP_PROOF,
                "referenceAvailable": True, "referenceContract": REFERENCE_PROOF}

    async def build(parameters):
        builds.append(dict(parameters))
        return "a" * 32

    gateway.upload_input = upload
    gateway.discover_speech, gateway.build_reference_speech = discover, build
    return a, b, gateway, factory, csrf, builds, submissions


def upload_reference(a, csrf):
    response = a.post("/api/generation/inputs/upload", content=b"fixture-reference-waveform",
                      headers={"Content-Type": "audio/wav", "X-CSRF-TOKEN": csrf})
    assert response.status_code == 201, response.text
    assert response.json()["mimeType"] == "audio/wav"
    assert response.json()["thumbnailUrl"] is None
    return response.json()["id"]


def test_owned_reference_upload_and_speech_preserve_replay_and_deletion_fences(clients, monkeypatch):
    a, b, gateway, factory, csrf, builds, submissions = reference_setup(clients, monkeypatch)
    handle = upload_reference(a, csrf)
    body = {"text": "Reference speech", "referenceInputId": handle, "requestId": REQUEST}
    assert a.post("/api/generation/speech/jobs", json=body).status_code == 403
    made = a.post("/api/generation/speech/jobs", json=body, headers={"X-CSRF-TOKEN": csrf})
    assert made.status_code == 201, made.text
    assert "reference_audio" not in made.text
    with factory() as db:
        execution = db.get(Execution, uuid.UUID(made.json()["id"]))
        row = db.get(ManagedInput, uuid.UUID(handle))
        assert execution.reference_input_id == row.id
        assert execution.workflow == "speech-reference"
        assert execution.request_snapshot["referenceInputId"] == handle
        assert execution.request_snapshot["referenceSha256"] == hashlib.sha256(b"fixture-reference-waveform").hexdigest()
        assert execution.request_snapshot["speechContract"] == REFERENCE_PROOF
        assert builds[0]["reference_audio"] == row.upstream_input_id
    assert b.get("/api/generation/inputs/" + handle).status_code == 404
    assert b.get("/api/generation/speech/requests/" + REQUEST).status_code == 404
    repeated = a.post("/api/generation/speech/jobs", json=body, headers={"X-CSRF-TOKEN": csrf})
    assert repeated.json()["id"] == made.json()["id"]
    assert len(submissions) == len(builds) == 1
    assert a.delete("/api/generation/inputs/" + handle, headers={"X-CSRF-TOKEN": csrf}).status_code == 409
    changed = {**body, "referenceInputId": str(uuid.uuid4())}
    assert a.post("/api/generation/speech/jobs", json=changed, headers={"X-CSRF-TOKEN": csrf}).status_code == 409


def test_reference_from_another_account_never_resolves_upstream(clients, monkeypatch):
    a, b, gateway, _, csrf, builds, submissions = reference_setup(clients, monkeypatch)
    handle = upload_reference(a, csrf)
    cb = b.get("/api/session").json()["csrfToken"]
    calls = list(gateway.input_calls)
    response = b.post("/api/generation/speech/jobs", json={
        "text": "Other account", "referenceInputId": handle, "requestId": REQUEST,
    }, headers={"X-CSRF-TOKEN": cb})
    assert response.status_code == 404
    assert gateway.input_calls == calls and not builds and not submissions


def test_unavailable_reference_model_never_falls_back_to_no_reference(clients, monkeypatch):
    a, _, gateway, _, csrf, builds, submissions = reference_setup(clients, monkeypatch)
    handle = upload_reference(a, csrf)

    async def discover():
        return {"available": True, "speechContract": HTTP_PROOF, "referenceAvailable": False}

    gateway.discover_speech = discover
    response = a.post("/api/generation/speech/jobs", json={
        "text": "Reference", "referenceInputId": handle, "requestId": REQUEST,
    }, headers={"X-CSRF-TOKEN": csrf})
    assert response.status_code == 503
    assert not builds and not submissions


def test_expiry_during_reference_build_prevents_generation(clients, monkeypatch):
    a, _, gateway, factory, csrf, _, submissions = reference_setup(clients, monkeypatch)
    handle = upload_reference(a, csrf)

    async def build(parameters):
        with factory() as db:
            row = db.get(ManagedInput, uuid.UUID(handle))
            row.expires_at = now() - timedelta(seconds=1)
            db.commit()
        return "a" * 32

    gateway.build_reference_speech = build
    response = a.post("/api/generation/speech/jobs", json={
        "text": "Reference", "referenceInputId": handle, "requestId": REQUEST,
    }, headers={"X-CSRF-TOKEN": csrf})
    assert response.status_code == 409 and not submissions
    with factory() as db:
        assert db.scalar(select(Execution)).last_known_status == "failed"


def catalogs():
    return {"descriptors": [reference_speech_descriptor(), speech_descriptor(True)]}, {
        "capabilities": [{"id": "speech.generate", "provider_id": "irodori", "available": True,
                          "workflow_templates": ["speech-reference", "speech-no-reference"]}]
    }


def test_matched_controller_reference_descriptor_and_strict_build():
    catalog, capabilities = catalogs()
    assert qualified_reference(catalog, capabilities)
    mutated = copy.deepcopy(catalog)
    mutated["descriptors"][0]["parameters"]["reference_audio"]["media_types"] = ["audio/mpeg"]
    assert not qualified_reference(mutated, capabilities)
    parameters = {"text": "Reference", "caption": "", "seconds": 10.0, "steps": 40, "seed": 0,
                  "reference_audio": "c" * 32}
    result = {"schema_version": 9, "template": "speech-reference", "workflow_id": "a" * 32,
              "speech": REFERENCE_PROOF, "parameters": parameters, "prompt": None}
    assert checked_reference_build(result, parameters) == "a" * 32
    for changes in ({"speech": HTTP_PROOF}, {"schema_version": True},
                    {"parameters": {**parameters, "reference_audio": "d" * 32}}):
        with pytest.raises(GatewayError):
            checked_reference_build({**result, **changes}, parameters)


@pytest.mark.asyncio
async def test_gateway_reference_discovery_checks_model_support(monkeypatch):
    monkeypatch.setenv("STUDIO_SPEECH_ENABLED", "true")
    catalog, capabilities = catalogs()
    support = [True]

    async def call(name, arguments=None):
        return {
            "system.health": {"healthy": True, "providers": [
                {"id": "irodori", "available": True, "reference_audio": support[0]}]},
            "capabilities.list": capabilities, "workflows.list": catalog,
        }[name]

    gateway = GenerationGateway()
    monkeypatch.setattr(gateway, "_json", call)
    assert (await gateway.discover_speech())["referenceAvailable"] is True
    support[0] = False
    status = await gateway.discover_speech()
    assert status["available"] is True and status["referenceAvailable"] is False
