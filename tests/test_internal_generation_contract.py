import base64
import hashlib
import io
import json
import uuid
from contextlib import asynccontextmanager

import httpx
import httpx2
import pytest
import pytest_asyncio
from flamoris_generation_controller.config import Settings
from flamoris_generation_controller.contracts import API_PATH
from flamoris_generation_controller.http_api import HTTPAPI
from flamoris_generation_controller.runtime import GenerationController
from PIL import Image
from starlette.applications import Starlette
from starlette.routing import Route

from flamoris_studio.gateway import GatewayError, GenerationGateway

TOKEN = "fixture-controller-service-credential-32"


@pytest_asyncio.fixture
async def matched(tmp_path, monkeypatch):
    models = tmp_path / "models/checkpoints"
    models.mkdir(parents=True)
    (models / "base.safetensors").write_bytes(b"fixture only")
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(buffer, format="PNG")
    image = buffer.getvalue()
    state = {"pending": [], "completed": False, "submissions": 0}

    def provider(request):
        path = request.url.path
        if path == "/queue" and request.method == "GET":
            return httpx.Response(
                200, json={"queue_running": [], "queue_pending": state["pending"]}
            )
        if path == "/prompt":
            state["submissions"] += 1
            body = json.loads(request.content)
            state["pending"] = [[1, "prompt-1", body["prompt"], {}, ["7"]]]
            return httpx.Response(
                200, json={"prompt_id": "prompt-1", "node_errors": {}}
            )
        if path == "/history/prompt-1":
            return httpx.Response(
                200,
                json={
                    "prompt-1": {
                        "status": {
                            "status_str": "success",
                            "completed": True,
                            "messages": [],
                        },
                        "outputs": {
                            "7": {
                                "images": [
                                    {
                                        "filename": "result.png",
                                        "subfolder": "flamoris",
                                        "type": "output",
                                    }
                                ]
                            }
                        },
                    }
                }
                if state["completed"]
                else {},
            )
        if path == "/view":
            return httpx.Response(
                200, content=image, headers={"Content-Type": "image/png"}
            )
        raise AssertionError(f"Unexpected provider request: {path}")

    shared_inputs = tmp_path / "comfy-inputs"
    shared_inputs.mkdir()
    controller = GenerationController(
        Settings(
            comfyui_input_root=shared_inputs,
            model_root=tmp_path / "models",
            workflow_dir=tmp_path / "recipes",
            output_dir=tmp_path / "outputs",
        ),
        transport=httpx.MockTransport(provider),
    )
    api = HTTPAPI(controller, TOKEN)
    app = Starlette(
        routes=[Route(API_PATH + "/{operation}", api.__call__, methods=["POST"])]
    )
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
        yield gateway, controller, state, image
    finally:
        await controller.close()


@pytest.mark.asyncio
async def test_real_controller_gateway_generation_and_bounded_assets(matched):
    gateway, controller, state, image = matched
    discovery = await gateway.discover()
    assert discovery["available"] and len(discovery["workflows"]) == 2
    recipe = await gateway.build(
        "text-to-image",
        {"checkpoint": "base.safetensors", "positive_prompt": "flowers"},
    )
    job = await gateway.submit(recipe)
    with pytest.raises(GatewayError) as error:
        await gateway.submit(recipe)
    assert error.value.code == "busy" and state["submissions"] == 1
    assert (await gateway.status(job["job_id"]))["status"] == "queued"
    state.update(completed=True, pending=[])
    assert (await gateway.result(job["job_id"]))["status"] == "completed"
    asset = (await gateway.assets(job["job_id"]))[0]
    assert await gateway.content(asset["asset_id"], 512 * 1024) == (image, "image/png")
    prepared = await gateway.prepare_asset(asset["asset_id"])
    chunk = await gateway.read_asset(
        asset["asset_id"], prepared["sha256"], 0, 256 * 1024
    )
    assert base64.b64decode(chunk["data_base64"]) == image
    assert hashlib.sha256(image).hexdigest() == prepared["sha256"]
    assert not controller.jobs.activity()["busy"]


@pytest.mark.asyncio
async def test_real_controller_gateway_upload_publication_and_delete(matched):
    gateway, _, _, image = matched
    record = await gateway.upload_input(uuid.uuid4().hex, image, "image/png")
    found = await gateway.get_input(record["input_id"])
    assert found["sha256"] == hashlib.sha256(image).hexdigest()
    assert found["source_kind"] == "upload" and found["source_asset_id"] is None
    await gateway.delete_input(record["input_id"])
    with pytest.raises(GatewayError):
        await gateway.get_input(record["input_id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("conditioning", [False, True])
async def test_registered_reference_gateway_controller_provider_flow(
    matched, conditioning
):
    from flamoris_generation_controller.registered_comfy import image_prompt
    from flamoris_generation_controller.reference_comfy import conditioning_prompt
    from flamoris_generation_controller.workflows import Conditioning, Parameters
    from flamoris_studio.workflow_contract import normalize_catalog

    gateway, controller, state, image = matched
    params = Parameters(checkpoint="base.safetensors", positive_prompt="A new scene")
    roles = (
        ["character_image", "style_image", "control_image"]
        if conditioning
        else ["reference_image"]
    )
    if conditioning:
        for folder, name in (
            ("ipadapter", "adapter.safetensors"),
            ("clip_vision", "vision.safetensors"),
            ("controlnet", "control.safetensors"),
        ):
            path = controller.settings.model_root / folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"metadata only")
        graph = conditioning_prompt(
            params,
            Conditioning(
                character_adapter="adapter.safetensors",
                style_adapter="adapter.safetensors",
                clip_vision="vision.safetensors",
                controlnet="control.safetensors",
            ),
            roles,
        )
    else:
        graph = image_prompt(params, reference=True)
    raw = controller.workflows.definitions.register("References", graph)
    selected = normalize_catalog({"descriptors": [raw]})[0]
    assert selected["selectable"] and selected["liveProviderVerified"] is False
    discovered = await gateway.discover()
    assert next(
        item for item in discovered["workflows"] if item["id"] == selected["id"]
    )["selectable"]
    if conditioning:
        encoder = controller.settings.model_root / "clip_vision/vision.safetensors"
        encoder.unlink()
        unavailable = await gateway.discover()
        entry = next(
            item for item in unavailable["workflows"] if item["id"] == selected["id"]
        )
        assert (
            not entry["selectable"]
            and entry["reason"] == "reference_models_unavailable"
        )
        assert state["submissions"] == 0
        encoder.write_bytes(b"metadata only")
    uploaded = {
        role: await gateway.upload_input(uuid.uuid4().hex, image, "image/png")
        for role in roles
    }
    built = await gateway.build_selected(
        selected, {role: value["input_id"] for role, value in uploaded.items()}
    )
    job = await gateway.submit(built)
    prompt = state["pending"][0][2]
    assert all(
        node["inputs"]["image"].startswith("flamoris-inputs/")
        for node in prompt.values()
        if node["class_type"] == "LoadImage"
    )
    assert state["submissions"] == 1
    state.update(completed=True, pending=[])
    assert (await gateway.result(job["job_id"]))["status"] == "completed"


@pytest.mark.asyncio
async def test_gateway_external_discovery_uses_exact_actor_and_no_media_download(matched):
    from flamoris_generation_controller.contracts import CallerContext
    from flamoris_generation_controller.provenance import ExternalProvenance

    gateway, controller, state, _ = matched
    actor = {"issuer": "hub.example", "subject": "client-a"}
    recipe = await gateway.build("text-to-image", {"checkpoint": "base.safetensors", "positive_prompt": "flowers"})
    job = await controller.invoke("jobs.submit", {"workflow_id": recipe},
                                 context=CallerContext.external(ExternalProvenance(**actor)))
    state.update(completed=True, pending=[])
    assert (await gateway.external_jobs({**actor, "subject": "foreign"}))["jobs"] == []
    result = await gateway.external_jobs(actor)
    assert result["jobs"] == [{"job_id": job["job_id"], "sequence": 1}]
    assert (await gateway.external_jobs(actor, result["next_cursor"]))["jobs"] == []
    assert state["submissions"] == 1
