import base64
import io
import re
import uuid
import wave
from contextlib import asynccontextmanager

import httpx
import httpx2
import pytest
from flamoris_generation_controller.config import Settings
from flamoris_generation_controller.contracts import API_PATH
from flamoris_generation_controller.http_api import HTTPAPI
from flamoris_generation_controller.runtime import GenerationController
from starlette.applications import Starlette
from starlette.routing import Route

from flamoris_studio.gateway import GenerationGateway

TOKEN = "fixture-controller-service-credential-32"


@pytest.mark.asyncio
async def test_studio_http_gateway_upload_reference_generate_and_retrieve(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_SPEECH_ENABLED", "true")
    monkeypatch.setenv("STUDIO_GENERATION_NAMESPACE", "")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * 2400)
    data = buffer.getvalue()
    uploaded, deleted = [], []

    async def provider(request):
        if request.url.path == "/health":
            return httpx.Response(200, json={
                "status": "ok", "runtime": {"loaded": True},
                "model": {"id": "irodori-tts", "hf_checkpoint": "Aratako/Irodori-TTS-v4.1-Small"},
            })
        if request.url.path == "/v1/audio/voices":
            key = re.search(rb"flamoris_[0-9a-f]{32}", request.content).group().decode()
            assert data in request.content
            uploaded.append(key)
            return httpx.Response(201, json={
                "id": key, "object": "voice_file", "filename": key + ".wav", "bytes": len(data),
            })
        if request.method == "DELETE":
            key = request.url.path.rsplit("/", 1)[-1]
            deleted.append(key)
            return httpx.Response(200, json={"id": key, "deleted": True})
        if request.url.path == "/v1/audio/speech":
            return httpx.Response(200, content=data, headers={"content-type": "audio/wav"})
        return httpx.Response(503)

    controller = GenerationController(Settings(
        model_root=tmp_path / "models", workflow_dir=tmp_path / "recipes",
        output_dir=tmp_path / "outputs", irodori_url="http://127.0.0.1:8088",
    ), transport=httpx.MockTransport(provider))
    api = HTTPAPI(controller, TOKEN)
    app = Starlette(routes=[Route(API_PATH + "/{operation}", api.__call__, methods=["POST"])])
    gateway = GenerationGateway()

    @asynccontextmanager
    async def connection():
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://controller.test" + API_PATH + "/",
            headers={"Authorization": "Bearer " + TOKEN},
        ) as client:
            yield client

    monkeypatch.setattr(gateway, "_connection", connection)
    try:
        assert (await gateway.discover_speech())["referenceAvailable"] is True
        reference = await gateway.upload_input(uuid.uuid4().hex, data, "audio/wav")
        assert reference["media_kind"] == "audio"
        recipe = await gateway.build_reference_speech({
            "text": "Reference", "caption": "", "seconds": 10.0, "steps": 40, "seed": 0,
            "reference_audio": reference["input_id"],
        })
        job = await gateway.submit(recipe)
        await controller.providers.get("irodori")._runs[job["provider_execution_id"]].worker
        assert (await gateway.status(job["job_id"]))["status"] == "completed"
        asset = (await gateway.assets(job["job_id"]))[0]
        prepared = await gateway.prepare_asset(asset["asset_id"])
        part = await gateway.read_asset(asset["asset_id"], prepared["sha256"], 0, 256 * 1024)
        assert base64.b64decode(part["data_base64"]) == data
        assert uploaded == deleted and len(uploaded) == 1
    finally:
        await controller.close()
