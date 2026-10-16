import copy
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, select

from flamoris_studio.db import Asset, Execution, LoginSession
from flamoris_studio.app import create_app
from flamoris_studio.gateway import GatewayError, GenerationGateway
from flamoris_studio.speech_contract import HTTP_PROOF, PROOF, SpeechRequest, checked_build, qualified_speech
from test_studio import clients, register
from test_multimedia_results import BytesGateway, install_bytes_gateway, output

JOB = "b" * 32
REQUEST = "00000000-0000-4000-8000-000000000001"
DESCRIPTOR = json.loads((Path(__file__).parent / "fixtures/speech-descriptor.json").read_text())
CAPABILITIES = {"capabilities": [{"id": "speech.generate", "provider_id": "irodori",
                                 "available": True, "workflow_templates": ["speech-no-reference"]}]}


def http_descriptor():
    descriptor = copy.deepcopy(DESCRIPTOR)
    descriptor.update(speech=HTTP_PROOF, qualification="configured-http-contract")
    return descriptor


def test_resident_http_contract_is_exact_and_distinct_from_cli():
    descriptor = http_descriptor()
    assert qualified_speech({"descriptors": [descriptor]}, CAPABILITIES)
    for proof in (
        {**HTTP_PROOF, "server_contract_revision": "a" * 40},
        {**HTTP_PROOF, "reference_audio": True},
        {**HTTP_PROOF, "candidates": True},
        {**HTTP_PROOF, "source_revision": PROOF["source_revision"]},
    ):
        assert not qualified_speech({"descriptors": [{**descriptor, "speech": proof}]}, CAPABILITIES)
    assert not qualified_speech({"descriptors": [{**descriptor, "qualification": "configured-local-resources"}]}, CAPABILITIES)
    parameters = SpeechRequest(text="こんにちは").model_dump()
    built = {"schema_version": 4, "workflow_id": "a" * 32, "template": "speech-no-reference",
             "parameters": parameters, "prompt": None, "speech": HTTP_PROOF}
    assert checked_build(built, parameters, HTTP_PROOF) == "a" * 32
    with pytest.raises(GatewayError):
        checked_build(built, parameters, PROOF)
    with pytest.raises(GatewayError):
        checked_build({**built, "speech": PROOF}, parameters, HTTP_PROOF)


@pytest.mark.asyncio
async def test_http_discovery_and_build_keep_the_selected_profile(monkeypatch):
    monkeypatch.setenv("STUDIO_SPEECH_ENABLED", "true")
    parameters = SpeechRequest(text="こんにちは").model_dump()
    calls = []

    async def call(name, arguments=None):
        calls.append((name, arguments))
        return {"system.health": {"healthy": True}, "capabilities.list": CAPABILITIES,
                "workflows.list": {"descriptors": [http_descriptor()]},
                "workflows.build": {"schema_version": 4, "workflow_id": "a" * 32,
                    "template": "speech-no-reference", "parameters": parameters,
                    "prompt": None, "speech": HTTP_PROOF}}[name]

    gateway = GenerationGateway()
    monkeypatch.setattr(gateway, "_json", call)
    assert await gateway.discover_speech() == {
        "available": True, "speechContract": HTTP_PROOF,
        "referenceAvailable": False,
        "referenceContract": {**HTTP_PROOF, "profile": "irodori-server-reference-v1", "reference_audio": True},
    }
    assert await gateway.build_speech(parameters, HTTP_PROOF) == "a" * 32
    assert calls[-1] == ("workflows.build", {"template": "speech-no-reference", "parameters": parameters})


def test_http_submission_persists_profile_and_preserves_owner_and_replay_fences(clients, monkeypatch):
    a, b, gateway, factory, csrf, builds, submissions = setup_speech(clients, monkeypatch)

    async def discover():
        return {"available": True, "speechContract": HTTP_PROOF}

    async def build(parameters, proof):
        assert proof == HTTP_PROOF
        builds.append(dict(parameters))
        return "a" * 32

    gateway.discover_speech, gateway.build_speech = discover, build
    body = {"text": "こんにちは", "requestId": REQUEST}
    response = a.post("/api/generation/speech/jobs", json=body, headers={"X-CSRF-TOKEN": csrf})
    assert response.status_code == 201, response.text
    with factory() as db:
        saved = db.get(Execution, uuid.UUID(response.json()["id"]))
        assert saved.request_snapshot["speechContract"] == HTTP_PROOF
    assert b.get(f"/api/generation/speech/requests/{REQUEST}").status_code == 404
    assert a.post("/api/generation/speech/jobs", json=body, headers={"X-CSRF-TOKEN": csrf}).json() == response.json()
    assert len(submissions) == len(builds) == 1


def test_exact_native_catalog_qualification_is_independent_from_image_attestation():
    assert qualified_speech({"descriptors": [DESCRIPTOR]}, CAPABILITIES)
    assert DESCRIPTOR["readiness"] == {"status": "not-attested"}
    for key, value in [("schema_version", True), ("provider_id", "other"), ("version", 2),
                       ("input_roles", [{"role": "reference_audio"}]),
                       ("speech", {**PROOF, "reference_audio": True}),
                       ("speech", {**PROOF, "source_revision": "a" * 40}),
                       ("readiness", {"state": "ready"})]:
        changed = copy.deepcopy(DESCRIPTOR)
        changed[key] = value
        assert not qualified_speech({"descriptors": [changed]}, CAPABILITIES)
    changed = copy.deepcopy(DESCRIPTOR)
    changed["parameters"]["text"]["max_bytes"] = 4096
    assert not qualified_speech({"descriptors": [changed]}, CAPABILITIES)
    assert not qualified_speech({"descriptors": [DESCRIPTOR, DESCRIPTOR]}, CAPABILITIES)
    unavailable = copy.deepcopy(CAPABILITIES)
    unavailable["capabilities"][0]["available"] = False
    assert not qualified_speech({"descriptors": [DESCRIPTOR]}, unavailable)


@pytest.mark.parametrize("values", [{"text": " "}, {"text": "hello\x00"}, {"text": "x" * 513},
    {"text": "ok", "seed": True}, {"text": "ok", "steps": True}, {"text": "ok", "seconds": float("nan")},
    {"text": "ok", "seed": 2**53}, {"text": "ok", "reference_audio": "private"}])
def test_speech_inputs_fail_closed(values):
    with pytest.raises(ValidationError):
        SpeechRequest.model_validate(values)


def test_bounded_unicode_and_build_proof_match_native_contract():
    parameters = SpeechRequest(text="こんにちは", caption="穏やかな声", seed=0).model_dump()
    build = {"schema_version": 4, "workflow_id": "a" * 32, "template": "speech-no-reference",
             "parameters": parameters, "prompt": None, "speech": PROOF}
    assert checked_build(build, parameters) == "a" * 32
    for key, value in [("schema_version", True), ("prompt", {}), ("speech", {}),
                       ("parameters", {**parameters, "seed": True})]:
        with pytest.raises(GatewayError):
            checked_build({**build, key: value}, parameters)


@pytest.mark.asyncio
async def test_default_speech_optin_never_contacts_upstream_and_enabled_route_checks_catalog(monkeypatch):
    calls = []

    async def call(name, arguments=None):
        calls.append(name)
        return {"system.health": {"healthy": True}, "capabilities.list": CAPABILITIES,
                "workflows.list": {"descriptors": [DESCRIPTOR]}}[name]

    gateway = GenerationGateway()
    monkeypatch.setattr(gateway, "_json", call)
    monkeypatch.delenv("STUDIO_SPEECH_ENABLED", raising=False)
    assert await gateway.discover_speech() == {"available": False} and calls == []
    monkeypatch.setenv("STUDIO_SPEECH_ENABLED", "true")
    assert await gateway.discover_speech() == {"available": True}
    assert calls == ["system.health", "capabilities.list", "workflows.list"]


def setup_speech(clients, monkeypatch):
    a, b, gateway, factory = clients
    csrf = register(a, "speech-a@example.test")
    register(b, "speech-b@example.test")
    monkeypatch.setenv("STUDIO_SPEECH_ENABLED", "true")
    builds, submissions = [], []

    async def discover():
        return {"available": True}

    async def build(parameters):
        builds.append(dict(parameters))
        return "a" * 32

    async def submit(workflow):
        submissions.append(workflow)
        with factory() as db:
            saved = db.scalar(select(Execution))
            assert saved.request_snapshot["seed"] == builds[-1]["seed"]
        return {"job_id": JOB, "status": "queued"}

    gateway.discover_speech, gateway.build_speech, gateway.submit = discover, build, submit
    return a, b, gateway, factory, csrf, builds, submissions


def test_owned_speech_submission_audio_catalog_range_and_download(clients, monkeypatch):
    a, b, gateway, factory, csrf, builds, submissions = setup_speech(clients, monkeypatch)
    source = BytesGateway()
    install_bytes_gateway(gateway, source, [output(port="audio", role="audio", role_index=0)])
    created = a.post("/api/generation/speech/jobs", json={"text": "こんにちは", "seed": 0, "requestId": REQUEST},
                     headers={"X-CSRF-TOKEN": csrf})
    assert created.status_code == 201, created.text
    made = created.json()
    assert made["category"] == "speech" and made["operation"] == "speech.generate"
    assert JOB not in created.text and submissions == ["a" * 32] and builds[0]["seed"] == 0
    assert b.get(f"/api/executions/{made['id']}/result").status_code == 404
    asset = a.get(f"/api/executions/{made['id']}/result").json()["assets"][0]
    assert asset["previewKind"] == "audio" and asset["outputRole"] == {"port": "audio", "role": "audio", "index": 0}
    assert b.get(asset["downloadUrl"]).status_code == 404
    assert a.get(asset["previewUrl"], headers={"Range": "bytes=2-5"}).content == b"2345"
    assert a.get(asset["downloadUrl"]).content == source.data


def test_unknown_submit_returns_persisted_owned_fence_without_replay(clients, monkeypatch):
    a, b, gateway, factory, csrf, builds, submissions = setup_speech(clients, monkeypatch)
    monkeypatch.setattr("flamoris_studio.speech.secrets.randbelow", lambda limit: 1234)

    async def ambiguous(workflow):
        submissions.append(workflow)
        raise GatewayError("unavailable")

    gateway.submit = ambiguous
    body = {"text": "こんにちは", "seed": None, "requestId": REQUEST}
    response = a.post("/api/generation/speech/jobs", json=body,
                      headers={"X-CSRF-TOKEN": csrf})
    assert response.status_code == 201 and response.json()["state"] == "submission_unknown"
    assert len(submissions) == 1 and builds[0]["seed"] == 1234
    assert a.post("/api/generation/speech/jobs", json=body,
                  headers={"X-CSRF-TOKEN": csrf}).json() == response.json()
    assert len(submissions) == 1 and len(builds) == 1
    assert a.get(f"/api/generation/speech/requests/{REQUEST}").json() == response.json()
    assert b.get(f"/api/generation/speech/requests/{REQUEST}").status_code == 404
    assert a.post("/api/generation/speech/jobs", json={**body, "text": "other"},
                  headers={"X-CSRF-TOKEN": csrf}).status_code == 409
    with factory() as db:
        saved = db.get(Execution, uuid.UUID(response.json()["id"]))
        assert saved.upstream_job_id is None and saved.request_snapshot["seed"] == 1234


def test_unavailable_or_mismatched_build_cannot_submit_and_speech_rejects_wrong_output(clients, monkeypatch):
    a, _, gateway, factory, csrf, builds, submissions = setup_speech(clients, monkeypatch)

    async def wrong(parameters):
        raise GatewayError("validation")

    gateway.build_speech = wrong
    assert a.post("/api/generation/speech/jobs", json={"text": "hello", "requestId": REQUEST},
                  headers={"X-CSRF-TOKEN": csrf}).status_code == 422
    assert submissions == []
    with factory() as db:
        assert db.scalar(select(Execution)).last_known_status == "failed"
    monkeypatch.setenv("STUDIO_SPEECH_ENABLED", "false")
    assert a.get("/api/generation/speech/discovery").json() == {"available": False}
    assert a.post("/api/generation/speech/jobs", json={"text": "hello", "requestId": str(uuid.uuid4())},
                  headers={"X-CSRF-TOKEN": csrf}).status_code == 503

    async def correct(parameters):
        builds.append(dict(parameters))
        return "a" * 32

    monkeypatch.setenv("STUDIO_SPEECH_ENABLED", "true")
    gateway.build_speech = correct
    made = a.post("/api/generation/speech/jobs", json={"text": "hello", "requestId": str(uuid.uuid4())},
                  headers={"X-CSRF-TOKEN": csrf}).json()
    # The default fake returns Image metadata; a Speech job cannot catalog it.
    assert a.get(f"/api/executions/{made['id']}/result").status_code == 422
    with factory() as db:
        assert db.scalar(select(Asset)) is None


def test_concurrent_same_speech_uuid_builds_and_submits_only_once(clients, monkeypatch):
    a, _, gateway, factory, csrf, builds, submissions = setup_speech(clients, monkeypatch)

    def send(index):
        with TestClient(create_app(factory, gateway, a.app.state.thumbnails)) as worker:
            worker.cookies.update(a.cookies)
            return worker.post("/api/generation/speech/jobs", json={"text": "hello", "requestId": REQUEST},
                               headers={"X-CSRF-TOKEN": csrf})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(send, range(2)))
    assert all(response.status_code == 201 for response in responses)
    assert responses[0].json()["id"] == responses[1].json()["id"]
    assert len(builds) == 1 and len(submissions) == 1
    with factory() as db:
        assert len(db.scalars(select(Execution)).all()) == 1


def test_configuration_change_after_build_cannot_submit(clients, monkeypatch):
    a, _, gateway, factory, csrf, _, submissions = setup_speech(clients, monkeypatch)

    async def changed(parameters):
        monkeypatch.setenv("STUDIO_SPEECH_ENABLED", "false")
        return "a" * 32

    gateway.build_speech = changed
    response = a.post("/api/generation/speech/jobs", json={"text": "hello", "requestId": REQUEST},
                      headers={"X-CSRF-TOKEN": csrf})
    assert response.status_code == 409 and submissions == []
    with factory() as db:
        assert db.scalar(select(Execution)).last_known_status == "failed"


def test_revoked_session_after_build_cannot_submit(clients, monkeypatch):
    a, _, gateway, factory, csrf, _, submissions = setup_speech(clients, monkeypatch)

    async def revoked(parameters):
        with factory() as db:
            db.execute(delete(LoginSession))
            db.commit()
        return "a" * 32

    gateway.build_speech = revoked
    response = a.post("/api/generation/speech/jobs", json={"text": "hello", "requestId": REQUEST},
                      headers={"X-CSRF-TOKEN": csrf})
    assert response.status_code == 401 and submissions == []
    with factory() as db:
        assert db.scalar(select(Execution)).last_known_status == "failed"



@pytest.mark.parametrize("seconds", [None, 3.5])
def test_automatic_and_fixed_speech_keep_distinct_strict_build_identity(seconds):
    parameters = SpeechRequest(text="こんにちは", seconds=seconds).model_dump()
    assert parameters["seconds"] == seconds
    built = {"schema_version": 4, "workflow_id": "a" * 32,
             "template": "speech-no-reference", "parameters": parameters,
             "prompt": None, "speech": HTTP_PROOF}
    assert checked_build(built, parameters, HTTP_PROOF) == "a" * 32
    with pytest.raises(GatewayError):
        checked_build({**built, "parameters": {**parameters, "seconds": 10.0}}, parameters, HTTP_PROOF)


def test_auto_duration_descriptor_does_not_accept_another_numeric_default():
    changed = copy.deepcopy(DESCRIPTOR)
    changed["parameters"]["seconds"]["default"] = 10
    assert not qualified_speech({"descriptors": [changed]}, CAPABILITIES)
