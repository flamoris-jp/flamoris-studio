"""Authenticated one-shot inference; durable dispatch fences, no upstream jobs."""

import asyncio
import hashlib
import json
import os
import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import authenticated_user, current_user, database
from .db import IntelligenceRequest
from .intelligence_gateway import IntelligenceError, IntelligenceGateway, endpoint_valid


class InferenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    requestId: str = Field(pattern=r"^[0-9a-f-]{36}$")
    modelId: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
    capabilityId: Literal["text.generate", "reasoning.generate", "code.generate"] = (
        "text.generate"
    )
    input: str = Field(min_length=1, max_length=16384)
    instruction: str = Field(default="", max_length=16384)
    maxOutputTokens: int = Field(default=1024, ge=1, le=32768)
    temperature: float = Field(default=0.7, ge=0, le=2, allow_inf_nan=False)

    @model_validator(mode="after")
    def limits(self):
        if (
            str(uuid.UUID(self.requestId)) != self.requestId
            or not self.input.strip()
            or len((self.input + self.instruction).encode()) > 16384
        ):
            raise ValueError("Invalid bounded inference input")
        return self

    def upstream(self):
        return {
            "model_id": self.modelId,
            "capability_id": self.capabilityId,
            "input": self.input,
            "instruction": self.instruction,
            "max_output_tokens": self.maxOutputTokens,
            "temperature": self.temperature,
            "timeout_seconds": 120.0,
        }


def configured():
    """Operator pins approved local targets; no implicit remote export or fallback."""
    try:
        endpoint = os.getenv("STUDIO_INTELLIGENCE_ENDPOINT", "")
        token = os.getenv("STUDIO_INTELLIGENCE_TOKEN", "")
        raw = os.getenv("STUDIO_INTELLIGENCE_MODELS", "[]")
        if (
            os.getenv("STUDIO_INTELLIGENCE_DATA_FLOW") != "approved-local"
            or len(raw.encode()) > 8192
        ):
            raise ValueError()
        endpoint_valid(endpoint, token)
        models = json.loads(raw)
        seen = set()
        if type(models) is not list or not 1 <= len(models) <= 16:
            raise ValueError()
        for model in models:
            if (
                type(model) is not dict
                or set(model)
                != {"model_id", "provider_id", "context_tokens", "max_output_tokens"}
                or type(model["model_id"]) is not str
                or not re.fullmatch(
                    r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", model["model_id"]
                )
                or model["model_id"] in seen
                or model["provider_id"] != "llamacpp"
                or type(model["context_tokens"]) is not int
                or not 1024 <= model["context_tokens"] <= 1048576
                or type(model["max_output_tokens"]) is not int
                or not 1 <= model["max_output_tokens"] <= 32768
                or model["max_output_tokens"] + 512 >= model["context_tokens"]
            ):
                raise ValueError()
            seen.add(model["model_id"])
        stamp = hashlib.sha256(
            json.dumps([endpoint, token, models], sort_keys=True).encode()
        ).hexdigest()
        return endpoint, token, models, stamp
    except (ValueError, TypeError, KeyError, UnicodeError, IntelligenceError):
        raise HTTPException(503, "Raw Intelligence is not configured") from None


async def bounded_json(request):
    body = bytearray()
    try:
        async with asyncio.timeout(5):
            async for part in request.stream():
                body.extend(part)
                if len(body) > 36 * 1024:
                    raise HTTPException(413, "Inference request too large")
        return InferenceInput.model_validate_json(bytes(body))
    except TimeoutError:
        raise HTTPException(408, "Inference request timed out") from None
    except (ValidationError, ValueError, UnicodeError):
        raise HTTPException(422, "Invalid inference request") from None


def mount_intelligence(app):
    router = APIRouter(prefix="/api/intelligence")
    app.state.intelligence_gateway_factory = lambda endpoint, token: (
        IntelligenceGateway(endpoint, token)
    )
    app.state.intelligence_slots = asyncio.Semaphore(1)
    app.state.intelligence_probe_slots = asyncio.Semaphore(2)

    def reauthorize(request, db, user, settings):
        db.rollback()
        if current_user(request, db) != user:
            raise HTTPException(401, "Session expired; result withheld")
        if configured() != settings:
            raise HTTPException(
                409, "Intelligence configuration changed; result withheld"
            )
        db.rollback()

    @router.get("/discovery")
    async def discovery(
        request: Request, user: uuid.UUID = Depends(authenticated_user)
    ):
        try:
            endpoint, token, models, _ = configured()
            if app.state.intelligence_probe_slots.locked():
                return {
                    "available": False,
                    "models": [],
                    "capabilities": [],
                    "reason": "busy",
                }
            async with app.state.intelligence_probe_slots, asyncio.timeout(60):
                gateway = app.state.intelligence_gateway_factory(endpoint, token)
                return await gateway.discover(models)
        except (HTTPException, IntelligenceError, TimeoutError):
            return {
                "available": False,
                "models": [],
                "capabilities": [],
                "reason": "unavailable",
            }

    @router.post("/execute")
    async def execute(
        request: Request,
        db: Session = Depends(database),
        user: uuid.UUID = Depends(authenticated_user),
    ):
        value = await bounded_json(request)
        settings = configured()
        if app.state.intelligence_slots.locked():
            raise HTTPException(409, "Intelligence request in progress")
        async with app.state.intelligence_slots:
            endpoint, token, models, stamp = settings
            pin = next((m for m in models if m["model_id"] == value.modelId), None)
            if pin is None:
                raise HTTPException(422, "Model is not approved")
            size = len((value.input + value.instruction).encode())
            if (
                value.maxOutputTokens > pin["max_output_tokens"]
                or size + 512 + value.maxOutputTokens > pin["context_tokens"]
            ):
                raise HTTPException(422, "Input and output budget exceed model limits")
            gateway = app.state.intelligence_gateway_factory(endpoint, token)
            # Freeze one request before any remote dispatch. Never persist prompts or results here.
            payload = value.upstream()
            fence = IntelligenceRequest(
                user_id=user,
                request_id=uuid.UUID(value.requestId),
                binding_digest=stamp,
                request_digest=hashlib.sha256(
                    json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest(),
                state="uncertain",
            )
            inserted = False

            async def admit():
                nonlocal inserted
                reauthorize(request, db, user, settings)
                db.add(fence)
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    raise HTTPException(
                        409, "Request already recorded; it will not be replayed"
                    ) from None
                inserted = True

            try:
                result = await gateway.execute(
                    payload, approved_model=pin, before_dispatch=admit
                )
                if not inserted:
                    raise IntelligenceError("invalid_result", uncertain=True)
                reauthorize(request, db, user, settings)
                fence = db.get(IntelligenceRequest, fence.id)
                fence.state = "completed"
                db.commit()
                return {"requestId": value.requestId, **result}
            except IntelligenceError as exc:
                if inserted and not exc.uncertain:
                    db.rollback()
                    fence = db.get(IntelligenceRequest, fence.id)
                    fence.state = "rejected"
                    db.commit()
                # Dispatch uncertainty is observable, never automatically retried.
                return JSONResponse(
                    status_code=503
                    if exc.uncertain or exc.code == "unavailable"
                    else 502,
                    content={
                        "error": "intelligence_unavailable",
                        "uncertain": bool(exc.uncertain),
                        "message": "Inference outcome is unconfirmed. No automatic retry was made."
                        if exc.uncertain
                        else "Intelligence request could not be executed.",
                    },
                )

    app.include_router(router)
