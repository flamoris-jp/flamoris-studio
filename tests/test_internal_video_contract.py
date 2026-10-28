"""Actual Controller HTTP integration with synthetic nodes and a tiny encoded MP4."""
import base64
import hashlib
import io
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import av
import httpx
import httpx2
import pytest
from flamoris_generation_controller.config import Settings
from flamoris_generation_controller.contracts import API_PATH, CallerContext
from flamoris_generation_controller.http_api import HTTPAPI
from flamoris_generation_controller.runtime import GenerationController
from PIL import Image
from starlette.applications import Starlette
from starlette.routing import Route

from flamoris_studio.gateway import GatewayError, GenerationGateway


def encoded_clip():
    output = io.BytesIO()
    with av.open(output, mode="w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=16)
        stream.width = stream.height = 64
        stream.pix_fmt = "yuv420p"
        for _ in range(5):
            for packet in stream.encode(av.VideoFrame.from_image(Image.new("RGB", (64, 64), "red"))):
                container.mux(packet)
        for packet in stream.encode(): container.mux(packet)
    return output.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["i2v", "flf2v"])
async def test_shared_controller_http_video_inputs_exact_recipe_and_verified_mp4(tmp_path, monkeypatch, mode):
    fixtures = Path(__file__).parent / "fixtures"
    fixture = json.loads((fixtures / "wan-video-contract.json").read_text())[mode]
    body, specs = fixture["registration"], fixture["object_info"]
    specs.update(json.loads((fixtures / "native-video-v3.json").read_text())["node_specs"])
    output = body["graph"]["6"]["inputs"]; output["format.codec"] = output.pop("codec")
    for target in body["declaration"]["constants"]:
        if target == {"node": "6", "input": "codec"}: target["input"] = "format.codec"
    clip = encoded_clip()
    state = {"prompt": None, "complete": False, "posts": 0}

    def provider(request):
        path = request.url.path
        if path.startswith("/object_info/"):
            name = path.rsplit("/", 1)[-1]; return httpx.Response(200, json={name: specs[name]})
        if path == "/queue":
            pending = [[1, "prompt-video", state["prompt"], {}, ["6"]]] if state["prompt"] and not state["complete"] else []
            return httpx.Response(200, json={"queue_running": [], "queue_pending": pending})
        if path == "/prompt":
            state["posts"] += 1; state["prompt"] = json.loads(request.content)["prompt"]
            return httpx.Response(200, json={"prompt_id": "prompt-video", "node_errors": {}})
        if path == "/history/prompt-video":
            prefix = state["prompt"]["6"]["inputs"]["filename_prefix"].split("/")[-1]
            return httpx.Response(200, json={"prompt-video": {"status": {"status_str": "success", "completed": True, "messages": []},
                "outputs": {"6": {"images": [{"filename": prefix + "_00001_.mp4", "subfolder": "flamoris", "type": "output"}]}}}} if state["complete"] else {})
        if path == "/view": return httpx.Response(200, content=clip, headers={"Content-Type": "video/mp4"})
        raise AssertionError(path)

    shared = tmp_path / "shared"; shared.mkdir()
    controller = GenerationController(Settings(model_root=tmp_path / "models", workflow_dir=tmp_path / "recipes", output_dir=tmp_path / "outputs", comfyui_input_root=shared, declared_comfy_nodes=list(specs)), transport=httpx.MockTransport(provider))
    token = "fixture-video-service-credential-32chars"
    api = HTTPAPI(controller, token)
    app = Starlette(routes=[Route(API_PATH + "/{operation}", api.__call__, methods=["POST"])])
    gateway = GenerationGateway()

    @asynccontextmanager
    async def connection():
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://controller.test" + API_PATH + "/", headers={"Authorization": "Bearer " + token}) as client:
            yield client

    monkeypatch.setattr(gateway, "_connection", connection)
    try:
        definition = await controller.invoke("comfy.register", body, context=CallerContext.internal())
        discovery = await gateway.discover_video()
        assert discovery["available"] and len(discovery["workflows"]) == 1
        item = discovery["workflows"][0]; assert item["id"] == definition["id"] and item["video"]["mode"] == mode
        buffer = io.BytesIO(); Image.new("RGB", (64, 64), "blue").save(buffer, "PNG")
        inputs = {role: (await gateway.upload_input(uuid.uuid4().hex, buffer.getvalue(), "image/png"))["input_id"] for role in item["referenceRoles"]}
        values = {name: spec["default"] for name, spec in item["parameters"].items() if spec["role"] != "managed_input"}
        recipe = await gateway.build_selected(item, {**values, **inputs})
        job = await gateway.submit(recipe)
        with pytest.raises(GatewayError): await gateway.submit(recipe)
        assert state["posts"] == 1
        assert all(node["inputs"]["image"].startswith("flamoris-inputs/") for node in state["prompt"].values() if node["class_type"] == "LoadImage")
        assert state["prompt"]["6"]["inputs"]["format.codec"] == "h264"
        state["complete"] = True
        assert (await gateway.result(job["job_id"]))["status"] == "completed"
        asset = (await gateway.assets(job["job_id"]))[0]
        assert asset["role"] == "video_clip" and asset["mime_type"] == "video/mp4"
        prepared = await gateway.prepare_asset(asset["asset_id"])
        part = await gateway.read_asset(asset["asset_id"], prepared["sha256"], 0, 256 * 1024)
        assert base64.b64decode(part["data_base64"]) == clip and prepared["sha256"] == hashlib.sha256(clip).hexdigest()
        assert not controller.jobs.activity()["busy"]
    finally:
        await controller.close()
