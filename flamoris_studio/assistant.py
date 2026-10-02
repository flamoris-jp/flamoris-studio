"""Authenticated Studio mappings and frozen one-shot advice, not Agent transcript authority."""

import asyncio
import hashlib
import json
import os
import re
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .agent_gateway import AgentError, AgentGateway
from .auth import current_user, database
from .db import AssistantRequest, AssistantSession, now


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    positive_prompt: StrictStr = Field(max_length=16384)
    negative_prompt: StrictStr = Field(default="", max_length=16384)
    width: StrictInt | None = Field(default=None, ge=64, le=4096)
    height: StrictInt | None = Field(default=None, ge=64, le=4096)
    steps: StrictInt | None = Field(default=None, ge=1, le=150)
    cfg: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    denoise: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    seed: StrictInt | None = Field(default=None, ge=0, le=18446744073709551615)

    @field_validator("width", "height")
    @classmethod
    def multiple_of_eight(cls, value):
        if value is not None and value % 8:
            raise ValueError("Invalid image dimensions")
        return value


class AdviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: uuid.UUID
    sessionKey: uuid.UUID
    text: StrictStr = Field(min_length=1, max_length=16384)
    previousHandle: uuid.UUID | None = None
    draft: Draft | None = None
    draftRevision: StrictInt = Field(default=1, ge=1, le=2147483647)

    @field_validator("text")
    @classmethod
    def text_bytes(cls, value):
        if not value.strip() or len(value.encode()) > 16384:
            raise ValueError("Question too large")
        return value


def configured_actor(user_id):
    try:
        configured = os.getenv("STUDIO_AGENT_BINDINGS", "[]")
        if len(configured.encode()) > 65536:
            raise ValueError()
        rows = json.loads(configured)
        if type(rows) is not list or len(rows) > 128:
            raise ValueError()
        users, principals, selected = set(), set(), None
        for row in rows:
            if type(row) is not dict or set(row) != {
                "user_id",
                "human",
                "agent",
                "project",
            }:
                raise ValueError()
            uid = str(uuid.UUID(row["user_id"]))
            keys = {k: row[k] for k in ("human", "agent", "project")}
            principal = tuple(keys.values())
            if (
                uid != row["user_id"]
                or uid in users
                or principal in principals
                or any(
                    type(v) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", v)
                    for v in principal
                )
            ):
                raise ValueError()
            users.add(uid)
            principals.add(principal)
            if uid == str(user_id):
                selected = keys
        endpoint, token = (
            os.getenv("STUDIO_AGENT_ENDPOINT", ""),
            os.getenv("STUDIO_AGENT_TOKEN", ""),
        )
        if selected is None or not endpoint or not token:
            raise ValueError()
        stamp = hashlib.sha256(
            json.dumps([endpoint, token, selected], sort_keys=True).encode()
        ).hexdigest()
        return selected, endpoint, token, stamp
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(
            503, "Assistant is not configured for this account"
        ) from None


def reauthorize(request, db, user, actor):
    db.rollback()
    if current_user(request, db) != user:
        raise HTTPException(401)
    if configured_actor(user) != actor:
        raise HTTPException(409, "Assistant binding changed; refresh before sending")


async def session_for(request, db, user, actor, gateway):
    cached = db.get(AssistantSession, user)
    if (
        cached
        and cached.binding_digest == actor[3]
        and cached.expires_at > now() + timedelta(seconds=20)
    ):
        return cached
    db.rollback()
    upstream, expiry = await gateway.open(actor[0])
    reauthorize(request, db, user, actor)
    if not now() + timedelta(seconds=20) < expiry <= now() + timedelta(hours=1):
        raise AgentError()
    db.rollback()
    cached = db.scalar(
        select(AssistantSession)
        .where(AssistantSession.user_id == user)
        .with_for_update()
    )
    if (
        cached
        and cached.binding_digest == actor[3]
        and cached.expires_at > now() + timedelta(seconds=20)
    ):
        return cached
    if cached is None:
        cached = AssistantSession(user_id=user)
        db.add(cached)
    cached.id, cached.upstream_session_id = uuid.uuid4(), uuid.UUID(upstream)
    cached.binding_digest, cached.expires_at = actor[3], expiry
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        cached = db.get(AssistantSession, user)
        if (
            cached is None
            or cached.binding_digest != actor[3]
            or cached.expires_at <= now() + timedelta(seconds=20)
        ):
            raise AgentError() from None
    return cached


async def bounded_body(request, limit):
    body = bytearray()
    try:
        async with asyncio.timeout(5):
            async for part in request.stream():
                body.extend(part)
                if len(body) > limit:
                    raise HTTPException(413, "Assistant request too large")
    except TimeoutError:
        raise HTTPException(408, "Assistant request timed out") from None
    return bytes(body)


def mount_assistant(app):
    router = APIRouter(prefix="/api/assistant")
    app.state.agent_gateway_factory = lambda endpoint, token: AgentGateway(
        endpoint, token
    )
    app.state.assistant_probe_slots = asyncio.Semaphore(2)
    app.state.assistant_ask_slots = asyncio.Semaphore(1)

    @router.post("/availability")
    async def availability(
        request: Request,
        response: Response,
        db: Session = Depends(database),
        user: uuid.UUID = Depends(current_user),
    ):
        response.headers["Cache-Control"] = "private, no-store"
        raw = await bounded_body(request, 1024)
        try:
            if raw and json.loads(raw) != {}:
                raise ValueError()
        except ValueError:
            raise HTTPException(
                422, "Availability takes no identity arguments"
            ) from None
        try:
            actor = configured_actor(user)
        except HTTPException:
            return {"available": False, "state": "unavailable"}
        slots = app.state.assistant_probe_slots
        try:
            await asyncio.wait_for(slots.acquire(), 0.01)
        except TimeoutError:
            return {"available": False, "state": "busy"}
        try:
            async with asyncio.timeout(40):
                gateway = app.state.agent_gateway_factory(actor[1], actor[2])
                binding = await session_for(request, db, user, actor, gateway)
                session_key, upstream, expiry = (
                    binding.id,
                    str(binding.upstream_session_id),
                    binding.expires_at,
                )
                db.rollback()
                state = await gateway.availability(upstream)
                reauthorize(request, db, user, actor)
                current = db.get(AssistantSession, user)
                if not current or current.id != session_key or expiry <= now():
                    raise AgentError()
                return {
                    **state,
                    "sessionKey": str(session_key),
                    "sessionExpiresAt": expiry.isoformat(),
                }
        except (AgentError, TimeoutError):
            return {"available": False, "state": "unavailable"}
        finally:
            slots.release()

    @router.post("/ask")
    async def ask(
        request: Request,
        response: Response,
        db: Session = Depends(database),
        user: uuid.UUID = Depends(current_user),
    ):
        response.headers["Cache-Control"] = "private, no-store"
        body = await bounded_body(request, 36 * 1024)
        try:
            advice = AdviceRequest.model_validate_json(bytes(body))
        except ValueError:
            raise HTTPException(422, "Invalid assistant request") from None
        actor = configured_actor(user)
        binding = db.get(AssistantSession, user)
        if (
            binding is None
            or binding.id != advice.sessionKey
            or binding.binding_digest != actor[3]
            or binding.expires_at <= now() + timedelta(seconds=5)
        ):
            raise HTTPException(
                409, "Assistant session expired or changed; refresh before sending"
            )
        parent = None
        if advice.previousHandle:
            previous = db.scalar(
                select(AssistantRequest).where(
                    AssistantRequest.id == advice.previousHandle,
                    AssistantRequest.user_id == user,
                )
            )
            if (
                previous is None
                or previous.state != "completed"
                or previous.session_id != binding.id
                or previous.binding_digest != actor[3]
            ):
                raise HTTPException(404, "Assistant conversation unavailable")
            parent = str(previous.upstream_conversation_id)
        payload = {
            "session_id": str(binding.upstream_session_id),
            "request_id": str(advice.requestId),
            "text": advice.text,
        }
        if parent:
            payload["previous_conversation_id"] = parent
        if advice.draft is not None:
            context = {
                "revision": 1,
                "category": "image",
                "operation": "image.generate",
                "product_context_id": str(binding.id),
                "draft_revision": advice.draftRevision,
                "draft": advice.draft.model_dump(exclude_none=True),
                "assets": [],
            }
            if len(json.dumps(context, ensure_ascii=False).encode()) > 16384:
                raise HTTPException(422, "Attached draft too large")
            payload["context"] = context
        # Freeze in memory; Studio persists references/hash only, never a transcript.
        payload = json.loads(json.dumps(payload, ensure_ascii=False))
        fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        session_key = binding.id
        db.rollback()
        slots = app.state.assistant_ask_slots
        try:
            await asyncio.wait_for(slots.acquire(), 0.01)
        except TimeoutError:
            raise HTTPException(409, "Assistant is busy") from None
        record = None
        try:
            gateway = app.state.agent_gateway_factory(actor[1], actor[2])
            state = await gateway.availability(payload["session_id"])
            reauthorize(request, db, user, actor)
            if not state["available"]:
                raise HTTPException(
                    409, "Assistant is not ready; question was not sent"
                )
            current = db.get(AssistantSession, user)
            if not current or current.id != session_key or current.expires_at <= now():
                raise HTTPException(409, "Assistant session changed")
            record = AssistantRequest(
                user_id=user,
                request_id=advice.requestId,
                session_id=session_key,
                binding_digest=actor[3],
                request_digest=fingerprint,
                state="uncertain",
            )
            db.add(record)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                raise HTTPException(
                    409, "Request already recorded; no replay was made"
                ) from None
            handle = record.id
            db.rollback()

            async def before_dispatch():
                if await request.is_disconnected():
                    raise AgentError("uncertain")
                reauthorize(request, db, user, actor)
                latest = db.get(AssistantSession, user)
                if not latest or latest.id != session_key or latest.expires_at <= now():
                    raise AgentError("uncertain")
                db.rollback()

            result = await gateway.ask(payload, before_dispatch=before_dispatch)
            reauthorize(request, db, user, actor)
            current = db.get(AssistantSession, user)
            if not current or current.id != session_key or current.expires_at <= now():
                raise HTTPException(409, "Assistant session changed; result withheld")
            record = db.get(AssistantRequest, handle)
            record.upstream_conversation_id, record.state = (
                uuid.UUID(result["conversation_id"]),
                "completed",
            )
            db.commit()
            return {
                "requestHandle": str(handle),
                "sessionKey": str(session_key),
                "text": result["text"],
                "provenance": result["provenance"],
            }
        except AgentError as exc:
            if record is not None:
                raise HTTPException(
                    503, "Assistant outcome could not be confirmed. No replay was made."
                ) from None
            raise HTTPException(
                503, "Assistant is unavailable; question was not sent"
            ) from exc
        finally:
            slots.release()

    app.include_router(router)
