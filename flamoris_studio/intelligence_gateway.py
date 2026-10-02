"""Explicit raw Intelligence MCP contract, separate from Agent conversations."""

import asyncio
import json
import re
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .agent_gateway import PRIVATE, AgentTransport

CAPABILITIES = {"text.generate", "reasoning.generate", "code.generate"}
CATALOG = {
    "system.health",
    "capabilities.list",
    "capabilities.get",
    "models.list",
    "models.get",
    "inference.execute",
}
INFERENCE_REQUEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["model_id", "input"],
    "properties": {
        "model_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "input": {"type": "string", "minLength": 1, "maxLength": 262144},
        "instruction": {"type": "string", "default": "", "maxLength": 262144},
        "capability_id": {
            "type": "string",
            "default": "text.generate",
            "enum": ["text.generate", "reasoning.generate", "code.generate"],
        },
        "max_output_tokens": {
            "type": "integer",
            "default": 1024,
            "minimum": 1,
            "maximum": 32768,
        },
        "temperature": {"type": "number", "default": 0.7, "minimum": 0, "maximum": 2},
        "timeout_seconds": {
            "default": None,
            "anyOf": [
                {"type": "number", "exclusiveMinimum": 0, "maximum": 300},
                {"type": "null"},
            ],
        },
    },
}


def semantic_schema(value):
    """Ignore presentation text, preserving every validation keyword."""
    if type(value) is dict:
        return {
            k: semantic_schema(v)
            for k, v in value.items()
            if k not in {"title", "description"}
        }
    if type(value) is list:
        return [semantic_schema(v) for v in value]
    return value


SAFE_CODES = {
    "invalid_request",
    "unknown_model",
    "unknown_capability",
    "context_limit",
    "busy",
    "provider_unavailable",
    "provider_timeout",
    "provider_rejected",
    "provider_unauthorized",
    "provider_busy",
    "invalid_provider_response",
    "response_limit",
    "internal_error",
}


class IntelligenceError(Exception):
    def __init__(self, code="unavailable", *, uncertain=False):
        super().__init__("Intelligence service unavailable")
        self.code, self.uncertain = code, uncertain


class IIntelligenceGateway(Protocol):
    async def discover(self, approved: list[dict]) -> dict: ...
    async def execute(
        self, request: dict, *, approved_model: dict, before_dispatch=None
    ) -> dict: ...


def endpoint_valid(endpoint, token):
    try:
        url = urlsplit(endpoint)
        if (
            len(endpoint) > 2048
            or url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or url.port is not None
            and not 1 <= url.port <= 65535
            or any(c.isspace() or ord(c) < 32 for c in endpoint)
            or "\\" in endpoint
            or "%" in url.netloc
            or url.scheme == "http"
            and url.hostname not in {"127.0.0.1", "::1", "localhost"}
            or token
            and not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", token)
            or url.scheme == "https"
            and not token
        ):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise IntelligenceError() from None


class IntelligenceGateway:
    def __init__(self, endpoint, token="", *, transport_factory=None):
        endpoint_valid(endpoint, token)
        self.endpoint, self.token = endpoint, token
        self.transport_factory = transport_factory or (
            lambda: AgentTransport(limit=1024 * 1024)
        )

    async def _call(
        self, name, args=None, *, timeout=20, before_dispatch=None, approved_model=None
    ):
        dispatched = False
        callback_started = callback_done = False
        marker = PRIVATE.set(True)
        try:
            async with asyncio.timeout(timeout):
                headers = {"Accept-Encoding": "identity"}
                if self.token:
                    headers["Authorization"] = f"Bearer {self.token}"
                async with httpx2.AsyncClient(
                    transport=self.transport_factory(),
                    trust_env=False,
                    follow_redirects=False,
                    timeout=httpx2.Timeout(timeout),
                    headers=headers,
                ) as http:
                    async with streamable_http_client(
                        self.endpoint, http_client=http, terminate_on_close=False
                    ) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            catalog = await session.list_tools()
                            if (
                                catalog.next_cursor is not None
                                or len(catalog.tools) != 6
                                or {t.name for t in catalog.tools} != CATALOG
                            ):
                                raise IntelligenceError("contract_mismatch")
                            tool = next(
                                t
                                for t in catalog.tools
                                if t.name == "inference.execute"
                            )
                            schema = tool.input_schema.get("properties", {}).get(
                                "request", {}
                            )
                            if (
                                set(tool.input_schema.get("properties", {}))
                                != {"request"}
                                or tool.input_schema.get("type") != "object"
                                or tool.input_schema.get("required") != ["request"]
                                or semantic_schema(schema) != INFERENCE_REQUEST_SCHEMA
                            ):
                                raise IntelligenceError("contract_mismatch")
                            if before_dispatch is not None:
                                model = await session.call_tool(
                                    "models.get",
                                    {"model_id": approved_model["model_id"]},
                                    read_timeout_seconds=20,
                                )
                                if model.is_error or not model_matches(
                                    model.structured_content, approved_model
                                ):
                                    raise IntelligenceError("contract_mismatch")
                                callback_started = True
                                await before_dispatch()
                                callback_done = True
                            dispatched = name == "inference.execute"
                            result = await session.call_tool(
                                name, args or {}, read_timeout_seconds=timeout
                            )
                            data = result.structured_content
                            if (
                                type(data) is not dict
                                or len(
                                    json.dumps(
                                        data, ensure_ascii=False, allow_nan=False
                                    ).encode()
                                )
                                > 256 * 1024
                            ):
                                raise IntelligenceError(
                                    "invalid_result", uncertain=dispatched
                                )
                            if result.is_error or data.get("ok") is False:
                                error = data.get("error")
                                code = (
                                    error.get("code") if type(error) is dict else None
                                )
                                if (
                                    not result.is_error
                                    or data.get("ok") is not False
                                    or code not in SAFE_CODES
                                ):
                                    raise IntelligenceError(
                                        "invalid_result", uncertain=dispatched
                                    )
                                # Fixed codes only; never forward upstream messages or private details.
                                safe_rejection = code in {
                                    "invalid_request",
                                    "unknown_model",
                                    "unknown_capability",
                                    "context_limit",
                                    "busy",
                                }
                                raise IntelligenceError(
                                    code, uncertain=dispatched and not safe_rejection
                                )
                            return data
        except IntelligenceError:
            raise
        except Exception as exc:
            # MCP task groups wrap domain/admission errors. Recover only our known
            # errors; unknown cleanup/transport failures remain ambiguous.
            error = exc
            while isinstance(error, BaseExceptionGroup) and len(error.exceptions) == 1:
                error = error.exceptions[0]
            if isinstance(error, IntelligenceError):
                raise error from None
            if (
                callback_started
                and not callback_done
                and not isinstance(error, BaseExceptionGroup)
            ):
                raise error from None
            raise IntelligenceError(uncertain=dispatched) from None
        finally:
            PRIVATE.reset(marker)

    async def discover(self, approved):
        raw = await self._call("models.list")
        caps = await self._call("capabilities.list")
        health = await self._call("system.health")
        models = raw.get("models")
        if (
            type(models) is not list
            or len(models) > 16
            or type(caps.get("capabilities")) is not list
            or len(caps["capabilities"]) != 3
            or any(
                type(c) is not dict or type(c.get("capability_id")) is not str
                for c in caps["capabilities"]
            )
            or {c["capability_id"] for c in caps["capabilities"]} != CAPABILITIES
            or health.get("healthy") is not True
            or type(health.get("providers")) is not list
        ):
            raise IntelligenceError("contract_mismatch")
        seen, selected = set(), []
        for model in models:
            if (
                type(model) is not dict
                or type(model.get("model_id")) is not str
                or model["model_id"] in seen
            ):
                raise IntelligenceError("contract_mismatch")
            seen.add(model["model_id"])
            pin = next(
                (p for p in approved if p["model_id"] == model["model_id"]), None
            )
            if pin is None:
                continue
            if not model_matches(model, pin):
                continue
            available = any(
                p.get("provider_id") == pin["provider_id"]
                and p.get("available") is True
                for p in health["providers"]
                if type(p) is dict
            )
            selected.append(
                {
                    "id": model["model_id"],
                    "contextTokens": pin["context_tokens"],
                    "maxOutputTokens": pin["max_output_tokens"],
                    "available": available,
                }
            )
        return {
            "available": any(m["available"] for m in selected),
            "models": selected,
            "capabilities": sorted(CAPABILITIES),
            "dataFlow": "approved-local",
        }

    async def execute(self, request, *, approved_model, before_dispatch=None):
        if approved_model["model_id"] != request["model_id"] or before_dispatch is None:
            raise IntelligenceError("contract_mismatch")
        data = await self._call(
            "inference.execute",
            {"request": request},
            timeout=150,
            before_dispatch=before_dispatch,
            approved_model=approved_model,
        )
        try:
            if (
                data.get("ok") is not True
                or str(UUID(data["execution_id"])) != data["execution_id"]
                or data.get("provider_id") != "llamacpp"
                or data.get("model_id") != request["model_id"]
                or data.get("capability_id") != request["capability_id"]
                or type(data.get("elapsed_ms")) is not int
                or not 0 <= data["elapsed_ms"] <= 300000
                or type(data.get("text")) is not str
                or len(data["text"].encode()) > 65536
                or data.get("finish_reason") not in {"stop", "length"}
                or data["finish_reason"] == "stop"
                and not data["text"].strip()
            ):
                raise ValueError()
            usage = data.get("usage")
            if usage is not None and (
                type(usage) is not dict
                or set(usage) != {"input_tokens", "output_tokens", "total_tokens"}
                or any(
                    type(v) is not int or not 0 <= v <= 1048576 for v in usage.values()
                )
                or usage["total_tokens"]
                != usage["input_tokens"] + usage["output_tokens"]
                or usage["output_tokens"] > request["max_output_tokens"]
            ):
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise IntelligenceError("invalid_result", uncertain=True) from None
        return {
            "text": data["text"],
            "finishReason": data["finish_reason"],
            "usage": usage,
            "elapsedMs": data["elapsed_ms"],
            "modelId": data["model_id"],
            "capabilityId": data["capability_id"],
            "executionId": data["execution_id"],
        }


def model_matches(model, pin):
    try:
        return (
            type(model) is dict
            and model.get("discovery") == "configured"
            and all(
                model.get(k) == v and type(model.get(k)) is type(v)
                for k, v in pin.items()
            )
            and type(model.get("capability_ids")) is list
            and len(model["capability_ids"]) == 3
            and set(model["capability_ids"]) == CAPABILITIES
        )
    except (TypeError, KeyError):
        return False
