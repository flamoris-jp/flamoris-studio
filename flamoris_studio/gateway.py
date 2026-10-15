"""Direct bounded Controller HTTP gateway; Studio retains user authorization."""

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx2

from .workflow_contract import normalize_catalog


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate upstream JSON field")
        value[key] = item
    return value


def _json_constant(_):
    raise ValueError("Nonfinite upstream JSON value")


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
            valid = bool(url.hostname) and not (
                url.username or url.password or url.query or url.fragment
            )
            valid = valid and url.path == "/api/v1/generation"
            valid = valid and (
                url.scheme == "https"
                or (
                    url.scheme == "http"
                    and url.hostname in {"127.0.0.1", "localhost", "::1"}
                    and port is not None
                )
            )
            valid = (
                valid
                and 32 <= len(token) <= 512
                and all(33 <= ord(c) <= 126 for c in token)
            )
            valid = valid and not os.getenv("STUDIO_GENERATION_NAMESPACE", "")
        except ValueError:
            valid = False
        if not valid:
            raise GatewayError("unavailable")
        try:
            async with httpx2.AsyncClient(
                base_url=endpoint + "/",
                headers={
                    "Authorization": "Bearer " + token,
                    "Accept-Encoding": "identity",
                },
                timeout=httpx2.Timeout(30, read=330),
                follow_redirects=False,
                trust_env=False,
            ) as http:
                yield http
        except GatewayError:
            raise
        except Exception as exc:
            raise GatewayError("unavailable") from exc

    async def _exchange(self, http, name, args, timeout, *, max_bytes=None):
        # The operation name is selected by backend code; no MCP envelope or user URL.
        operations = {
            "system.health",
            "capabilities.list",
            "capabilities.get",
            "models.list",
            "models.get",
            "workflows.list",
            "workflows.build",
            "workflows.save",
            "jobs.submit",
            "jobs.status",
            "jobs.external.list",
            "jobs.result",
            "jobs.cancel",
            "assets.list",
            "assets.delete",
            "assets.get",
            "assets.prepare",
            "assets.read",
            "inputs.create",
            "inputs.get",
            "inputs.delete",
            "inputs.upload.begin",
            "inputs.upload.write",
            "inputs.upload.finish",
        }
        if name not in operations:
            raise GatewayError("validation")
        async with (
            asyncio.timeout(timeout),
            http.stream(
                "POST", name, json=args or {}, timeout=httpx2.Timeout(30, read=timeout)
            ) as response,
        ):
            if 300 <= response.status_code < 400:
                raise GatewayError("unavailable")
            if response.headers.get("content-encoding", "identity") != "identity":
                raise GatewayError("upstream_failure")
            binary = max_bytes is not None and response.status_code == 200
            limit = max_bytes if binary else 2 * 1024**2
            length = response.headers.get("content-length")
            if length is not None and (not length.isdecimal() or int(length) > limit):
                raise GatewayError("asset_too_large" if binary else "upstream_failure")
            media_type = (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            if binary:
                if media_type not in {"image/png", "image/jpeg", "image/webp"}:
                    raise GatewayError("upstream_failure")
            elif media_type != "application/json":
                if response.status_code == 404 and name in {
                    "assets.prepare",
                    "assets.read",
                }:
                    raise GatewayError("transfer_unavailable")
                raise GatewayError("upstream_failure")
            content = bytearray()
            async for chunk in response.aiter_bytes():
                if len(content) + len(chunk) > limit:
                    raise GatewayError(
                        "asset_too_large" if binary else "upstream_failure"
                    )
                content.extend(chunk)
            if length is not None and len(content) != int(length):
                raise GatewayError("upstream_failure")
            if binary:
                return bytes(content), media_type
            try:
                data = json.loads(
                    content,
                    object_pairs_hook=_json_object,
                    parse_constant=_json_constant,
                )
            except (ValueError, RecursionError):
                raise GatewayError("upstream_failure") from None
            if type(data) is not dict:
                raise GatewayError("upstream_failure")
            if response.status_code != 200:
                error = data.get("error")
                code = error.get("code") if type(error) is dict else None
                if response.status_code == 409 and code == "busy":
                    raise GatewayError("busy")
                if response.status_code == 413 and code == "asset_too_large":
                    raise GatewayError("asset_too_large")
                if response.status_code == 404 and name in {
                    "assets.prepare",
                    "assets.read",
                }:
                    raise GatewayError("transfer_unavailable")
                if response.status_code == 400 and code == "validation":
                    raise GatewayError("validation")
                if response.status_code in {401, 403, 503}:
                    raise GatewayError("unavailable")
                raise GatewayError("upstream_failure")
            return data

    async def _call(
        self, name: str, args: dict | None = None, timeout: int = 45, *, max_bytes=None
    ):
        started = time.monotonic()
        if os.getenv("STUDIO_GENERATION_NAMESPACE", ""):
            raise GatewayError("unavailable")
        try:
            # httpx's read timeout resets on each chunk. Bound the whole call,
            # including connection setup, headers, streaming and cleanup.
            async with asyncio.timeout(timeout), self._connection() as http:
                return await self._exchange(
                    http, name, args, timeout, max_bytes=max_bytes
                )
        except GatewayError as exc:
            if name in {"assets.get", "assets.prepare", "assets.read"}:
                logging.getLogger(__name__).warning(
                    "Generation %s failed: code=%s elapsed=%.1fs",
                    name,
                    exc.code,
                    time.monotonic() - started,
                )
            raise
        except Exception as exc:
            # This includes ambiguous jobs.submit failures. Never retry or fall back.
            raise GatewayError("unavailable") from exc

    async def _json(self, name: str, args: dict | None = None) -> dict:
        data = await self._call(name, args)
        if not isinstance(data, dict):
            raise GatewayError("upstream_failure")
        return data

    async def discover(self):
        health = await self._json("system.health")
        if not health.get("healthy"):
            return {"available": False, "templates": [], "checkpoints": [], "loras": []}
        caps = await self._json("capabilities.list")
        found = next(
            (
                x
                for x in caps.get("capabilities", [])
                if x.get("id") == "image.generate"
            ),
            {},
        )
        if not found.get("available"):
            return {"available": False, "templates": [], "checkpoints": [], "loras": []}
        checkpoints = await self._json("models.list", {"kind": "checkpoint"})
        loras = await self._json("models.list", {"kind": "lora"})
        catalog = await self._json("workflows.list")
        try:
            descriptors = normalize_catalog(catalog)
            if health.get("managed_input_support", {}).get("ready") is not True:
                for descriptor in descriptors:
                    if descriptor.get("referenceRoles"):
                        descriptor.update(selectable=False, reason="reference_storage_unavailable")
        except (ValueError, TypeError):
            raise GatewayError("upstream_failure") from None
        conditioned = [d for d in descriptors if d.get("recipeSchema") == 8]
        if conditioned:
            discovered_models = await self._json("models.list", {"kind": None})
            installed = {(m.get("kind"), m.get("name")) for m in discovered_models.get("models", []) if isinstance(m, dict)}
            for descriptor in conditioned:
                required = {(spec.get("model_kind"), spec.get("default")) for spec in descriptor["parameters"].values()
                            if spec.get("model_kind") in {"checkpoint", "ipadapter", "clip_vision", "controlnet"}}
                if not required <= installed:
                    descriptor.update(selectable=False, reason="reference_models_unavailable")
        return {
            "available": True,
            "workflows": descriptors,
            "managedInputReady": health.get("managed_input_support", {}).get("ready")
            is True,
            "templates": found.get("workflow_templates", []),
            "checkpoints": [
                {"id": x["id"], "name": x["name"]} for x in checkpoints["models"]
            ],
            "loras": [{"id": x["id"], "name": x["name"]} for x in loras["models"]],
        }

    async def build(self, template: str, parameters: dict):
        if template not in {"text-to-image", "text-to-image-lora"}:
            raise GatewayError("retired_recipe")
        result = await self._json(
            "workflows.build", {"template": template, "parameters": parameters}
        )
        return result["workflow_id"]

    async def build_selected(self, descriptor, parameters):
        registered = descriptor.get("kind") == "registered-comfy"
        if registered:
            if (re.fullmatch(r"comfy-[a-f0-9]{64}", descriptor.get("id", "")) is None
                or descriptor.get("definitionVersion") != 1
                or descriptor.get("definitionDigest") != descriptor["id"][6:]
                or descriptor.get("recipeSchema") not in {7, 8}):
                raise GatewayError("validation")
        elif (descriptor.get("kind") != "builtin"
              or descriptor.get("id") not in {"text-to-image", "text-to-image-lora"}
              or descriptor.get("definitionVersion") is not None
              or descriptor.get("definitionDigest") is not None):
            raise GatewayError("retired_recipe")
        args = {"template": descriptor["id"], "parameters": parameters}
        if registered:
            args.update(definition_version=1, definition_digest=descriptor["definitionDigest"])
        result = await self._json("workflows.build", args)
        if registered:
            schema = descriptor["recipeSchema"]
            expected_images = {key: parameters[key] for key, spec in descriptor["parameters"].items()
                               if spec.get("role") in {"initial_image", "character_image", "style_image", "control_image"}}
            if (result.get("template") != "registered-comfy"
                or result.get("definition_id") != descriptor["id"]
                or type(result.get("schema_version")) is not int
                or result.get("schema_version") != schema):
                raise GatewayError("validation")
            if schema == 7:
                if result.get("reference_image") != parameters.get("reference_image"):
                    raise GatewayError("validation")
            elif result.get("images") != expected_images:
                raise GatewayError("validation")
            image_values = result.get("parameters")
            controls = result.get("conditioning", {}) if schema == 8 else {}
            if not isinstance(image_values, dict) or not isinstance(controls, dict):
                raise GatewayError("validation")
            for key, value in parameters.items():
                if key not in expected_images and ({**image_values, **controls}.get(key) != value):
                    raise GatewayError("validation")
        elif (result.get("template") != descriptor["id"]
              or result.get("parameters") != parameters or type(result.get("schema_version")) is not int
              or result.get("schema_version") != 1):
            raise GatewayError("validation")
        workflow_id = result.get("workflow_id")
        if not isinstance(workflow_id, str) or not re.fullmatch(
            r"[a-f0-9]{32}", workflow_id
        ):
            raise GatewayError("validation")
        return workflow_id

    async def create_input(self, asset_id):
        return await self._json("inputs.create", {"asset_id": asset_id})

    async def upload_input(self, upload_id, data, mime_type):
        if (
            not isinstance(upload_id, str)
            or not re.fullmatch(r"[0-9a-f]{32}", upload_id)
            or not isinstance(data, bytes)
            or not 0 < len(data) <= 8 * 1024**2
            or mime_type not in {"image/png", "image/jpeg", "image/webp", "audio/wav"}
        ):
            raise GatewayError("validation")
        if os.getenv("STUDIO_GENERATION_NAMESPACE", ""):
            raise GatewayError("unavailable")
        async with asyncio.timeout(75), self._connection() as http:

            async def call(name, arguments):
                try:
                    return await self._exchange(http, name, arguments, 15)
                except GatewayError:
                    raise
                except Exception as exc:
                    raise GatewayError("unavailable") from exc

            raw = await call(
                "inputs.upload.begin",
                {
                    "upload_id": upload_id,
                    "mime_type": mime_type,
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                },
            )
            offset = raw.get("offset")
            if (
                raw.get("upload_id") != upload_id
                or type(offset) is not int
                or not 0 <= offset <= len(data)
            ):
                raise GatewayError("validation")
            while offset < len(data):
                # Bounded chunks preserve ordered acknowledgements and digest checks.
                part = data[offset : offset + 128 * 1024]
                raw = await call(
                    "inputs.upload.write",
                    {
                        "upload_id": upload_id,
                        "offset": offset,
                        "data_base64": base64.b64encode(part).decode("ascii"),
                        "chunk_sha256": hashlib.sha256(part).hexdigest(),
                    },
                )
                if (
                    raw.get("upload_id") != upload_id
                    or type(raw.get("offset")) is not int
                    or raw["offset"] != offset + len(part)
                ):
                    raise GatewayError("validation")
                offset = raw["offset"]
            return await call("inputs.upload.finish", {"upload_id": upload_id})

    async def get_input(self, input_id):
        return await self._json("inputs.get", {"input_id": input_id})

    async def delete_input(self, input_id):
        return await self._json("inputs.delete", {"input_id": input_id})

    async def submit(self, workflow_id: str):
        return await self._json("jobs.submit", {"workflow_id": workflow_id})

    async def discover_speech(self):
        if os.getenv("STUDIO_SPEECH_ENABLED", "false").lower() != "true":
            return {"available": False}
        from .speech_contract import HTTP_PROOF, REFERENCE_PROOF, exact, qualified_speech, qualified_reference

        health = await self._json("system.health")
        if health.get("healthy") is not True:
            return {"available": False}
        caps = await self._json("capabilities.list")
        catalog = await self._json("workflows.list")
        available = qualified_speech(catalog, caps)
        if available and any(
            exact(item.get("speech"), HTTP_PROOF) for item in catalog["descriptors"]
            if item.get("id") == "speech-no-reference"
        ):
            return {
                "available": True, "speechContract": HTTP_PROOF,
                "referenceAvailable": qualified_reference(catalog, caps) and any(
                    type(item) is dict and item.get("id") == "irodori"
                    and item.get("available") is True and item.get("reference_audio") is True
                    for item in health.get("providers", [])
                ),
                "referenceContract": REFERENCE_PROOF,
            }
        return {"available": available}

    async def build_speech(self, parameters, proof=None):
        from .speech_contract import PROOF, TEMPLATE, checked_build

        return checked_build(
            await self._json(
                "workflows.build", {"template": TEMPLATE, "parameters": parameters}
            ),
            parameters,
            PROOF if proof is None else proof,
        )

    async def build_reference_speech(self, parameters):
        from .speech_contract import REFERENCE_TEMPLATE, checked_reference_build

        return checked_reference_build(
            await self._json(
                "workflows.build", {"template": REFERENCE_TEMPLATE, "parameters": parameters}
            ), parameters,
        )

    async def discover_music(self):
        if os.getenv("STUDIO_MUSIC_ENABLED", "false").lower() != "true":
            return {"generate": False, "transcribe": False}
        from .music_contract import qualified_music

        health = await self._json("system.health")
        if type(health) is not dict or health.get("healthy") is not True:
            return {"generate": False, "transcribe": False}
        caps = await self._json("capabilities.list")
        catalog = await self._json("workflows.list")
        return {
            "generate": qualified_music(catalog, caps, "generate"),
            "transcribe": qualified_music(catalog, caps, "transcribe")
            and type(health.get("managed_input_support")) is dict
            and health["managed_input_support"].get("ready") is True,
        }

    async def build_music(self, parameters, operation):
        from .music_contract import checked_music_build, contract

        return checked_music_build(
            await self._json(
                "workflows.build",
                {"template": contract(operation)["id"], "parameters": parameters},
            ),
            parameters,
            operation,
        )

    async def status(self, job_id: str):
        return await self._json("jobs.status", {"job_id": job_id})

    async def external_jobs(self, actor: dict, after: int = 0, limit: int = 8):
        return await self._json("jobs.external.list", {"actor": actor, "after": after, "limit": limit})

    async def result(self, job_id: str):
        return await self._json("jobs.result", {"job_id": job_id})

    async def cancel(self, job_id: str):
        return await self._json("jobs.cancel", {"job_id": job_id})

    async def assets(self, job_id: str):
        return (await self._json("assets.list", {"job_id": job_id}))["assets"]

    async def delete_asset(self, asset_id: str):
        return await self._json("assets.delete", {"asset_id": asset_id})

    async def content(self, asset_id: str, max_bytes: int):
        if type(max_bytes) is not int or not 0 < max_bytes <= 64 * 1024**2:
            raise GatewayError("validation")
        return await self._call(
            "assets.get", {"asset_id": asset_id}, timeout=300, max_bytes=max_bytes
        )

    async def prepare_asset(self, asset_id: str):
        return await self._json_transfer(
            "assets.prepare", {"asset_id": asset_id}, timeout=330
        )

    async def read_asset(self, asset_id: str, sha256: str, offset: int, length: int):
        return await self._json_transfer(
            "assets.read",
            {
                "asset_id": asset_id,
                "sha256": sha256,
                "offset": offset,
                "length": length,
            },
            timeout=45,
        )

    async def _json_transfer(self, name: str, args: dict, timeout: int):
        result = await self._call(name, args, timeout=timeout)
        if not isinstance(result, dict):
            raise GatewayError("upstream_failure")
        return result

