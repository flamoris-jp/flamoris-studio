"""Authenticated Studio mappings and frozen one-shot advice, not Agent transcript authority."""

import asyncio
import hashlib
import json
import os
import re
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .agent_gateway import AgentError, AgentGateway
from .auth import authenticated_user, current_user, database
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
                    type(v) is not str
                    or len(v) > 64
                    or not re.fullmatch(
                        r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*"
                        if k == "project"
                        else r"[A-Za-z0-9_-]{1,64}",
                        v,
                    )
                    for k, v in keys.items()
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


async def session_for(request, db, user, actor, gateway, *, selection=None):
    cached = db.get(AssistantSession, user)
    if (
        selection is None
        and cached
        and cached.binding_digest == actor[3]
        and cached.expires_at > now() + timedelta(seconds=20)
    ):
        return cached
    if selection is None and os.getenv("STUDIO_AGENT_SETTINGS_ENABLED") == "1":
        raise HTTPException(409, "Select a model and start a conversation first")
    db.rollback()
    keys = (
        actor[0]
        if selection is None
        else {
            **actor[0],
            "model_id": selection.modelId,
            "remote_consent": selection.remoteConsent,
        }
    )
    upstream, expiry = await gateway.open(keys)
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
        selection is None
        and cached
        and cached.binding_digest == actor[3]
        and cached.expires_at > now() + timedelta(seconds=20)
    ):
        return cached
    if cached is None:
        cached = AssistantSession(user_id=user)
        db.add(cached)
    cached.id, cached.upstream_session_id = uuid.uuid4(), uuid.UUID(upstream)
    cached.binding_digest, cached.expires_at = actor[3], expiry
    cached.model_id = selection.modelId if selection else None
    cached.remote_consent = selection.remoteConsent if selection else False
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


class ModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    modelId: StrictStr = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    remoteConsent: StrictBool = False


class PersonalitySection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: StrictStr = Field(min_length=1, max_length=80)
    content: StrictStr = Field(min_length=1, max_length=32768)


class PersonalityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sessionKey: uuid.UUID
    requestId: uuid.UUID
    expectedRevision: StrictInt = Field(ge=1, le=256)
    displayName: StrictStr = Field(min_length=1, max_length=128)
    sections: list[PersonalitySection] = Field(min_length=1, max_length=16)


def settings_enabled():
    if os.getenv("STUDIO_AGENT_SETTINGS_ENABLED") != "1":
        raise HTTPException(503, "Assistant settings are unavailable")


def owned_binding(db, user, actor, session_key=None):
    binding = db.get(AssistantSession, user)
    if (
        binding is None
        or binding.binding_digest != actor[3]
        or binding.expires_at <= now()
        or session_key is not None
        and binding.id != session_key
    ):
        raise HTTPException(409, "Assistant session changed; reload before editing")
    return binding


def mount_assistant(app):
    router = APIRouter(prefix="/api/assistant")
    app.state.agent_gateway_factory = lambda endpoint, token: AgentGateway(
        endpoint, token
    )
    app.state.assistant_probe_slots = asyncio.Semaphore(2)
    app.state.assistant_ask_slots = asyncio.Semaphore(1)
    app.state.assistant_settings_slots = asyncio.Semaphore(2)

    async def settings_slot():
        slots = app.state.assistant_settings_slots
        try:
            await asyncio.wait_for(slots.acquire(), 0.01)
        except TimeoutError:
            raise HTTPException(429, "Assistant settings are busy") from None
        try:
            yield
        finally:
            slots.release()

    @router.post("/models", dependencies=[Depends(settings_slot)])
    async def models(
        request: Request,
        response: Response,
        db: Session = Depends(database),
        user: uuid.UUID = Depends(authenticated_user),
    ):
        settings_enabled()
        response.headers["Cache-Control"] = "private, no-store"
        raw = await bounded_body(request, 1024)
        if raw and raw != b"{}":
            raise HTTPException(422, "Model discovery takes no identity arguments")
        actor = configured_actor(user)
        try:
            result = await app.state.agent_gateway_factory(actor[1], actor[2]).models(
                actor[0]
            )
            reauthorize(request, db, user, actor)
            return result
        except AgentError:
            raise HTTPException(503, "Model catalog unavailable") from None

    @router.post("/start", dependencies=[Depends(settings_slot)])
    async def start(
        request: Request,
        response: Response,
        db: Session = Depends(database),
        user: uuid.UUID = Depends(authenticated_user),
    ):
        settings_enabled()
        response.headers["Cache-Control"] = "private, no-store"
        try:
            selection = ModelSelection.model_validate_json(
                await bounded_body(request, 1024)
            )
        except ValueError:
            raise HTTPException(422, "Invalid model selection") from None
        actor = configured_actor(user)
        gateway = app.state.agent_gateway_factory(actor[1], actor[2])
        try:
            catalog = await gateway.models(actor[0])
            reauthorize(request, db, user, actor)
            chosen = next(
                (m for m in catalog["models"] if m["id"] == selection.modelId), None
            )
            if not chosen:
                raise HTTPException(403, "Model is not permitted")
            if (
                chosen["data_flow"] == "remote_authorized"
                and not selection.remoteConsent
            ):
                raise HTTPException(422, "Confirm remote context transmission")
            binding = await session_for(
                request, db, user, actor, gateway, selection=selection
            )
            return {
                "sessionKey": str(binding.id),
                "modelId": selection.modelId,
                "remoteConsent": selection.remoteConsent,
            }
        except AgentError:
            raise HTTPException(
                503, "Could not start selected conversation; no retry was made"
            ) from None

    async def personality_read(operation, request, response, db, user):
        settings_enabled()
        response.headers["Cache-Control"] = "private, no-store"
        actor = configured_actor(user)
        body = await bounded_body(request, 1024)
        try:
            raw = json.loads(body or b"{}")
            if type(raw) is not dict or set(raw) - {"beforeRevision"}:
                raise ValueError()
            before = raw.get("beforeRevision")
            if before is not None and (
                type(before) is not int or not 1 <= before <= 257
            ):
                raise ValueError()
        except ValueError:
            raise HTTPException(422, "Invalid personality history request") from None
        binding = owned_binding(db, user, actor)
        session_key, upstream = binding.id, str(binding.upstream_session_id)
        db.rollback()
        payload = {"session_id": upstream}
        if before is not None:
            payload["before_revision"] = before
        try:
            result = await app.state.agent_gateway_factory(
                actor[1], actor[2]
            ).personality(operation, payload)
            reauthorize(request, db, user, actor)
            owned_binding(db, user, actor, session_key)
            return {**result, "sessionKey": str(session_key)}
        except AgentError as exc:
            raise HTTPException(
                403 if exc.code == "personality_forbidden" else 503,
                "Personality unavailable",
            ) from None

    @router.post("/personality", dependencies=[Depends(settings_slot)])
    async def personality_get(
        request: Request,
        response: Response,
        db: Session = Depends(database),
        user: uuid.UUID = Depends(authenticated_user),
    ):
        return await personality_read("get", request, response, db, user)

    @router.post("/personality/history", dependencies=[Depends(settings_slot)])
    async def personality_history(
        request: Request,
        response: Response,
        db: Session = Depends(database),
        user: uuid.UUID = Depends(authenticated_user),
    ):
        return await personality_read("history", request, response, db, user)

    @router.post("/personality/save", dependencies=[Depends(settings_slot)])
    async def personality_save(
        request: Request,
        response: Response,
        db: Session = Depends(database),
        user: uuid.UUID = Depends(authenticated_user),
    ):
        settings_enabled()
        response.headers["Cache-Control"] = "private, no-store"
        try:
            update = PersonalityUpdate.model_validate_json(
                await bounded_body(request, 64 * 1024)
            )
        except ValueError:
            raise HTTPException(422, "Invalid personality update") from None
        if sum(len(s.content.encode()) for s in update.sections) > 32768:
            raise HTTPException(422, "Personality exceeds context size limit")
        actor = configured_actor(user)
        binding = owned_binding(db, user, actor, update.sessionKey)
        session_key, upstream = binding.id, str(binding.upstream_session_id)
        db.rollback()

        async def before_dispatch():
            if await request.is_disconnected():
                raise AgentError("uncertain")
            reauthorize(request, db, user, actor)
            owned_binding(db, user, actor, session_key)
            db.rollback()

        payload = {
            "session_id": upstream,
            "request_id": str(update.requestId),
            "expected_revision": update.expectedRevision,
            "display_name": update.displayName,
            "sections": [s.model_dump() for s in update.sections],
        }
        try:
            result = await app.state.agent_gateway_factory(
                actor[1], actor[2]
            ).personality("save", payload, before_dispatch=before_dispatch)
            reauthorize(request, db, user, actor)
            owned_binding(db, user, actor, session_key)
            return result
        except AgentError as exc:
            if exc.code == "revision_conflict":
                raise HTTPException(
                    409, "Personality changed. Your unsaved draft was not overwritten."
                ) from None
            if exc.code == "personality_forbidden":
                raise HTTPException(
                    403, "Personality editing is not permitted"
                ) from None
            raise HTTPException(
                503,
                "Save outcome unconfirmed. Keep the same update identity when checking or retrying.",
            ) from None

    @router.post("/availability")
    async def availability(
        request: Request,
        response: Response,
        db: Session = Depends(database),
        user: uuid.UUID = Depends(authenticated_user),
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
                    "modelId": current.model_id,
                    "remoteConsent": current.remote_consent,
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
        user: uuid.UUID = Depends(authenticated_user),
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
