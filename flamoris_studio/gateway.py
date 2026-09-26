"""The only module allowed to see upstream MCP payloads and SDK objects."""
import os
from contextlib import asynccontextmanager
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
        if not endpoint.startswith(("https://", "http://127.0.0.1:", "http://localhost:")):
            raise GatewayError("unavailable")
        try:
            async with streamable_http_client(endpoint) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
        except GatewayError:
            raise
        except Exception as exc:
            raise GatewayError("unavailable") from exc

    async def _call(self, name: str, args: dict | None = None):
        try:
            async with self._connection() as session:
                result = await session.call_tool(name, args or {}, read_timeout_seconds=45)
            if result.is_error:
                # Upstream error strings are never forwarded to browser.
                message = " ".join(getattr(item, "text", "") for item in result.content)
                code = "busy" if "busy" in message.lower() else "upstream_failure"
                if name == "assets.get" and any(
                    marker in message.lower() for marker in ("retrieval limit", "download limit")
                ):
                    code = "asset_too_large"
                raise GatewayError(code)
            return result
        except GatewayError:
            raise
        except Exception as exc:
            # This includes ambiguous jobs.submit failures. Never retry automatically.
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
        return {"available": True, "templates": found.get("workflow_templates", []),
                "checkpoints": [{"id": x["id"], "name": x["name"]} for x in checkpoints["models"]],
                "loras": [{"id": x["id"], "name": x["name"]} for x in loras["models"]]}

    async def build(self, template: str, parameters: dict):
        result = await self._json("workflows.build", {"template": template, "parameters": parameters})
        return result["workflow_id"]

    async def submit(self, workflow_id: str):
        return await self._json("jobs.submit", {"workflow_id": workflow_id})

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
        result = await self._call("assets.get", {"asset_id": asset_id})
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
