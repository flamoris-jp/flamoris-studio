"""The only module allowed to see upstream MCP payloads and SDK objects."""
import os
import logging
import re
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
import httpx2
from .workflow_contract import normalize_catalog
from .image_v3_contract import normalize_v3_catalog
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


class GatewayError(Exception):
    def __init__(self, code: str):
        super().__init__("Generation service error")
        self.code = code


class GenerationGateway:
    @asynccontextmanager
    async def _connection(self):
        endpoint = os.getenv("STUDIO_GENERATION_ENDPOINT", "")
        token = os.getenv("STUDIO_GENERATION_TOKEN", "")
        try:
            url = urlsplit(endpoint)
            port = url.port
            valid = bool(url.hostname) and not (url.username or url.password or url.query or url.fragment)
            valid = valid and (url.scheme == "https" or (
                url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"} and port is not None))
            valid = valid and len(token) <= 4096 and all(33 <= ord(c) <= 126 for c in token)
        except ValueError:
            valid = False
        if not valid:
            raise GatewayError("unavailable")
        try:
            headers = {"Authorization": "Bearer " + token} if token else {}
            async def reject_redirect(response):
                # MCP can follow same-origin redirects independently of the client
                # flag. Reject before its transport can forward a token or replay POST.
                if 300 <= response.status_code < 400:
                    await response.aclose()
                    raise GatewayError("unavailable")
            async with httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(30, read=330),
                                          follow_redirects=False, trust_env=False,
                                          event_hooks={"response": [reject_redirect]}) as http:
                async with streamable_http_client(endpoint, http_client=http) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        yield session
        except GatewayError:
            raise
        except Exception as exc:
            raise GatewayError("unavailable") from exc

    async def _call(self, name: str, args: dict | None = None, timeout: int = 45):
        started = time.monotonic()
        namespace = os.getenv("STUDIO_GENERATION_NAMESPACE", "")
        if namespace not in {"", "generation"}:
            raise GatewayError("unavailable")
        wire_name = f"{namespace}.{name}" if namespace else name
        try:
            async with self._connection() as session:
                result = await session.call_tool(wire_name, args or {}, read_timeout_seconds=timeout)
            if result.is_error:
                # Upstream error strings are never forwarded to browser.
                message = " ".join(getattr(item, "text", "") for item in result.content)
                code = "busy" if "busy" in message.lower() else "upstream_failure"
                # The Hub uses this exact response for an unregistered tool. Do not
                # classify arbitrary upstream text or transport failures as missing capability.
                if name in {"assets.prepare", "assets.read"} and message.strip() == f"Unknown tool: {wire_name}":
                    code = "transfer_unavailable"
                if name == "assets.get" and any(
                    marker in message.lower() for marker in ("retrieval limit", "download limit")
                ):
                    code = "asset_too_large"
                raise GatewayError(code)
            return result
        except GatewayError as exc:
            if name in {"assets.get", "assets.prepare", "assets.read"}:
                logging.getLogger(__name__).warning("Generation %s failed: code=%s elapsed=%.1fs",
                                                     name, exc.code, time.monotonic() - started)
            raise
        except Exception as exc:
            # This includes ambiguous jobs.submit failures. Never retry automatically.
            if name in {"assets.get", "assets.prepare", "assets.read"}:
                logging.getLogger(__name__).warning("Generation %s transport failed: type=%s elapsed=%.1fs",
                                                     name, type(exc).__name__, time.monotonic() - started)
            raise GatewayError("unavailable") from exc

    async def _json(self, name: str, args: dict | None = None) -> dict:
        result = await self._call(name, args)
        data = result.structured_content
        if not isinstance(data, dict):
            raise GatewayError("upstream_failure")
        return data

    async def discover(self):
        health = await self._json("system.health")
        if not health.get("healthy"):
            return {"available": False, "templates": [], "checkpoints": [], "loras": []}
        caps = await self._json("capabilities.list")
        found = next((x for x in caps.get("capabilities", []) if x.get("id") == "image.generate"), {})
        if not found.get("available"):
            return {"available": False, "templates": [], "checkpoints": [], "loras": []}
        checkpoints = await self._json("models.list", {"kind": "checkpoint"})
        loras = await self._json("models.list", {"kind": "lora"})
        catalog = await self._json("workflows.list")
        try:
            descriptors = normalize_catalog(catalog)
            if os.getenv("STUDIO_GENERATION_V3_ENABLED", "false").lower() == "true":
                descriptors += normalize_v3_catalog(await self._json("workflows.v3.list"))
                if len(descriptors) > 128 or len({d["id"] for d in descriptors}) != len(descriptors):
                    raise ValueError("unsupported_parameter")
        except (ValueError, TypeError):
            raise GatewayError("upstream_failure") from None
        return {"available": True, "workflows": descriptors,
                "managedInputReady": health.get("managed_input_support", {}).get("ready") is True, "templates": found.get("workflow_templates", []),
                "checkpoints": [{"id": x["id"], "name": x["name"]} for x in checkpoints["models"]],
                "loras": [{"id": x["id"], "name": x["name"]} for x in loras["models"]]}

    async def build(self, template: str, parameters: dict):
        result = await self._json("workflows.build", {"template": template, "parameters": parameters})
        return result["workflow_id"]

    async def build_selected(self, descriptor, parameters):
        args = {"template": descriptor["id"], "parameters": parameters}
        v3 = descriptor["kind"] == "v3"
        if v3:
            if (os.getenv("STUDIO_GENERATION_V3_ENABLED", "false").lower() != "true"
                or not re.fullmatch(r"v3:[a-z][a-z0-9_-]{0,63}", descriptor["id"])):
                raise GatewayError("validation")
            args = {"workflow_id": descriptor["id"].removeprefix("v3:"), "parameters": parameters,
                    "definition_version": descriptor["definitionVersion"],
                    "definition_digest": descriptor["definitionDigest"], "require_ready": True}
        if descriptor["kind"] == "definition":
            args.update(definition_version=descriptor["definitionVersion"],
                        definition_digest=descriptor["definitionDigest"], require_ready=True)
        result = await self._json("workflows.v3.build" if v3 else "workflows.build", args)
        expected_template = descriptor["id"].removeprefix("v3:") if v3 else descriptor["id"]
        if result.get("template") != expected_template or result.get("parameters") != parameters:
            raise GatewayError("validation")
        if descriptor["kind"] in {"definition", "v3"} and (
            result.get("definition_version") != descriptor["definitionVersion"] or
            result.get("definition_digest") != descriptor["definitionDigest"] or
            result.get("require_ready") is not True):
            raise GatewayError("validation")
        if v3 and (any(type(result.get(k)) is not int for k in (
                       "schema_version", "definition_version", "compiler_revision", "adapter_revision"))
                   or result.get("schema_version") != 3 or result.get("compiler_revision") != 2
                   or result.get("adapter_revision") != 1 or any(
                       not isinstance(result.get(k), str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", result[k])
                       for k in ("structural_digest", "closure_digest", "invocation_digest"))):
            raise GatewayError("validation")
        workflow_id = result.get("workflow_id")
        if not isinstance(workflow_id, str) or not re.fullmatch(r"[a-f0-9]{32}", workflow_id):
            raise GatewayError("validation")
        return workflow_id

    async def create_input(self, asset_id):
        return await self._json("inputs.create", {"asset_id": asset_id})

    async def get_input(self, input_id):
        return await self._json("inputs.get", {"input_id": input_id})

    async def delete_input(self, input_id):
        return await self._json("inputs.delete", {"input_id": input_id})

    async def submit(self, workflow_id: str):
        return await self._json("jobs.submit", {"workflow_id": workflow_id})

    async def discover_speech(self):
        if os.getenv("STUDIO_SPEECH_ENABLED", "false").lower() != "true":
            return {"available": False}
        from .speech_contract import qualified_speech
        health = await self._json("system.health")
        if health.get("healthy") is not True:
            return {"available": False}
        caps = await self._json("capabilities.list")
        catalog = await self._json("workflows.list")
        return {"available": qualified_speech(catalog, caps)}

    async def build_speech(self, parameters):
        from .speech_contract import TEMPLATE, checked_build
        return checked_build(await self._json("workflows.build", {
            "template": TEMPLATE, "parameters": parameters}), parameters)

    async def discover_music(self):
        if os.getenv("STUDIO_MUSIC_ENABLED", "false").lower() != "true":
            return {"generate": False, "transcribe": False}
        from .music_contract import qualified_music
        health = await self._json("system.health")
        if type(health) is not dict or health.get("healthy") is not True:
            return {"generate": False, "transcribe": False}
        caps = await self._json("capabilities.list")
        catalog = await self._json("workflows.list")
        return {"generate": qualified_music(catalog, caps, "generate"),
                "transcribe": qualified_music(catalog, caps, "transcribe") and
                    type(health.get("managed_input_support")) is dict and
                    health["managed_input_support"].get("ready") is True}

    async def build_music(self, parameters, operation):
        from .music_contract import contract, checked_music_build
        return checked_music_build(await self._json("workflows.build", {
            "template": contract(operation)["id"], "parameters": parameters}), parameters, operation)

    async def status(self, job_id: str):
        return await self._json("jobs.status", {"job_id": job_id})

    async def result(self, job_id: str):
        return await self._json("jobs.result", {"job_id": job_id})

    async def cancel(self, job_id: str):
        return await self._json("jobs.cancel", {"job_id": job_id})

    async def assets(self, job_id: str):
        return (await self._json("assets.list", {"job_id": job_id}))["assets"]

    async def delete_asset(self, asset_id: str):
        return await self._json("assets.delete", {"asset_id": asset_id})

    async def content(self, asset_id: str, max_bytes: int):
        result = await self._call("assets.get", {"asset_id": asset_id}, timeout=300)
        images = [item for item in result.content if item.type == "image"]
        if len(images) != 1:
            raise GatewayError("upstream_failure")
        image = images[0]
        if len(image.data) > 4 * ((max_bytes + 2) // 3) + 4:
            raise GatewayError("asset_too_large")
        import base64
        try:
            data = base64.b64decode(image.data, validate=True)
        except Exception as exc:
            raise GatewayError("upstream_failure") from exc
        if len(data) > max_bytes:
            raise GatewayError("asset_too_large")
        return data, image.mime_type

    async def prepare_asset(self, asset_id: str):
        return await self._json_transfer("assets.prepare", {"asset_id": asset_id}, timeout=330)

    async def read_asset(self, asset_id: str, sha256: str, offset: int, length: int):
        return await self._json_transfer("assets.read", {
            "asset_id": asset_id, "sha256": sha256, "offset": offset, "length": length}, timeout=45)

    async def _json_transfer(self, name: str, args: dict, timeout: int):
        result = await self._call(name, args, timeout=timeout)
        if not isinstance(result.structured_content, dict):
            raise GatewayError("upstream_failure")
        return result.structured_content
