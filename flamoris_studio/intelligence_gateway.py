"""Studio policy wrapper over the shared non-MCP intelligence adapters."""

import re
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

from flamoris_intelligence import (
    IntelligenceError as ProviderError,
)
from flamoris_intelligence import (
    ModelEntry,
    Settings,
    create_service,
)

CAPABILITIES = {"text.generate", "reasoning.generate", "code.generate"}
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
            or url.path not in {"", "/"}
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
        self.endpoint, self.token = endpoint.rstrip("/"), token
        self.transport_factory = transport_factory

    def _service(self, approved):
        # This configuration is operator-owned. The client cannot choose a URL,
        # credential, provider alias or change the approved resource limits.
        settings = Settings(
            provider_url=self.endpoint,
            provider_api_key=self.token or None,
            models=tuple(
                ModelEntry(
                    id=pin["model_id"],
                    provider_id=pin["provider_id"],
                    provider_model=pin["model_id"],
                    context_tokens=pin["context_tokens"],
                    max_output_tokens=pin["max_output_tokens"],
                )
                for pin in approved
            ),
            timeout_seconds=120,
            health_timeout_seconds=5,
            max_input_bytes=16384,
            max_output_bytes=65536,
            max_response_bytes=1024 * 1024,
            max_concurrency=1,
        )
        return create_service(
            settings,
            transport=self.transport_factory() if self.transport_factory else None,
        )

    async def discover(self, approved):
        service = self._service(approved)
        try:
            selected = []
            for pin in approved:
                available = False
                try:
                    model = await service.model_available(pin["model_id"])
                    available = model_matches(model, pin)
                except ProviderError:
                    pass
                selected.append(
                    {
                        "id": pin["model_id"],
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
        except Exception:  # noqa: BLE001 -- private discovery diagnostics stay internal
            raise IntelligenceError() from None
        finally:
            await service.close()

    async def execute(self, request, *, approved_model, before_dispatch=None):
        if approved_model["model_id"] != request["model_id"] or before_dispatch is None:
            raise IntelligenceError("contract_mismatch")
        service = self._service([approved_model])
        dispatched = False
        callback_started = callback_done = False
        try:
            model = await service.model_available(approved_model["model_id"])
            if not model_matches(model, approved_model):
                raise IntelligenceError("contract_mismatch")
            callback_started = True
            await before_dispatch()
            callback_done = True
            dispatched = True
            data = await service.execute(request)
            if type(data) is not dict:
                raise IntelligenceError("invalid_result", uncertain=True)
            if data.get("ok") is not True:
                error = data.get("error")
                code = error.get("code") if type(error) is dict else None
                if code not in SAFE_CODES or data.get("ok") is not False:
                    raise IntelligenceError("invalid_result", uncertain=True)
                safe_rejection = code in {
                    "invalid_request",
                    "unknown_model",
                    "unknown_capability",
                    "context_limit",
                    "busy",
                }
                raise IntelligenceError(code, uncertain=not safe_rejection)
        except IntelligenceError:
            raise
        except ProviderError as exc:
            raise IntelligenceError(
                exc.code if exc.code in SAFE_CODES else "unavailable",
                uncertain=dispatched,
            ) from None
        except Exception:
            if callback_started and not callback_done:
                raise
            raise IntelligenceError(uncertain=dispatched) from None
        finally:
            await service.close()
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
