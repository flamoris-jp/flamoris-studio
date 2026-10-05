"""Studio HTTP boundary: all user-owned lookups include the authenticated owner."""
import os
import secrets
import re
import asyncio
import hashlib
import uuid
from datetime import timedelta
from contextlib import asynccontextmanager

from argon2.exceptions import VerificationError
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from .auth import COOKIE, CSRF_COOKIE, DUMMY_PASSWORD_HASH, authenticated_user, clear_session, current_user, database, digest, hasher, new_csrf, require_csrf, start_session
from .db import Asset, Execution, ExternalAssetClaim, ExternalImport, ImagePreference, ImageStyle, LoginSession, User, make_session_factory, now
from .gateway import GatewayError, GenerationGateway
from .media import Thumbnails, filename, generated_filename, inspect_image
from .logging_setup import configure_logging
from .managed_inputs import (Limits, owned_input, usable, input_view, reserve, check_input,
    create_snapshot, maintenance, prune, protected, quota_guard)
from .workflow_contract import ROLES, map_parameters
from .input_thumbnails import InputThumbnails
from .input_uploads import mount_input_uploads
from .transfer import CHUNK_BYTES, MAX_TRANSFER_BYTES, PreviewAdmission, chunk, metadata, read_with_retry
from .result_contract import normalize_outputs, output_role, preview_kind
from .range_transfer import OwnedStreamingResponse, representation_response
from .assistant import mount_assistant
from .intelligence import mount_intelligence
from .login_throttle import admit_login
from .external_import import catalog_guard, mount_external_import
from .speech import mount_speech
from .music import mount_music
from .music_contract import checked_music_outputs

# Small known images use direct binary retrieval; larger or unknown sizes use
# bounded transfer. Keep this consumer threshold separate from the media limit.
NATIVE_IMAGE_BYTES = 512 * 1024
MAX_SAFE_IMAGE_SEED = 2**53 - 1


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=256)
    password: str = Field(min_length=12, max_length=256)


class EmailChange(BaseModel):
    email: str = Field(min_length=3, max_length=256)
    currentPassword: str = Field(min_length=1, max_length=256)


class PasswordChange(BaseModel):
    currentPassword: str = Field(min_length=1, max_length=256)
    newPassword: str = Field(min_length=12, max_length=256)
    confirmPassword: str = Field(min_length=12, max_length=256)


def normalized_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > 256 or "@" not in email or any(char.isspace() for char in email):
        raise HTTPException(422, "Invalid email address")
    return email


class DeleteAssetsRequest(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=32)


class Lora(BaseModel):
    name: str = Field(min_length=1, max_length=1024)
    strengthModel: float = Field(default=1, ge=-20, le=20, allow_inf_nan=False)
    strengthClip: float = Field(default=1, ge=-20, le=20, allow_inf_nan=False)


class ImageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    positivePrompt: str = Field(min_length=1, max_length=20000)
    negativePrompt: str = Field(default="", max_length=20000)
    width: int = Field(ge=64, le=4096, multiple_of=8)
    height: int = Field(ge=64, le=4096, multiple_of=8)
    steps: int = Field(ge=1, le=150)
    cfg: float = Field(ge=0, le=100, allow_inf_nan=False)
    seed: int | None = Field(default=None, ge=0, le=MAX_SAFE_IMAGE_SEED)
    checkpoint: str = Field(min_length=1, max_length=1024)
    loras: list[Lora] = Field(default_factory=list, max_length=16)
    sampler: str = Field(default="euler", pattern=r"^[a-zA-Z0-9_]+$", max_length=80)
    scheduler: str = Field(default="normal", pattern=r"^[a-zA-Z0-9_]+$", max_length=80)
    denoise: float = Field(default=1, ge=0, le=1, allow_inf_nan=False)

    def parameters(self):
        return {"positive_prompt": self.positivePrompt, "negative_prompt": self.negativePrompt,
                "width": self.width, "height": self.height, "steps": self.steps,
                "cfg": self.cfg, "seed": self.seed, "checkpoint": self.checkpoint,
                "sampler": self.sampler, "scheduler": self.scheduler, "denoise": self.denoise,
                "loras": [{"name": item.name, "strength_model": item.strengthModel,
                           "strength_clip": item.strengthClip} for item in self.loras]}


class WorkflowImageRequest(ImageRequest):
    # Keep scalar types exact before evaluating the descriptor-owned constraints.
    width: int = Field(ge=64, le=4096, multiple_of=8, strict=True)
    height: int = Field(ge=64, le=4096, multiple_of=8, strict=True)
    steps: int | None = Field(default=None, strict=True)
    cfg: float | None = Field(default=None, allow_inf_nan=False, strict=True)
    seed: int | None = Field(default=None, ge=0, le=MAX_SAFE_IMAGE_SEED, strict=True)
    denoise: float | None = Field(default=None, allow_inf_nan=False, strict=True)
    sampler: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_]+$", max_length=80)
    scheduler: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_]+$", max_length=80)
    workflowId: str = Field(min_length=1, max_length=128)
    workflowKind: str = Field(pattern="^builtin$")
    # Retain nullable DTO fields for existing builtin clients. Non-null legacy
    # definition pins are rejected before any reservation or upstream request.
    definitionVersion: None = None
    definitionDigest: None = None
    referenceInputId: None = None
    additionalParameters: dict = Field(default_factory=dict, max_length=64)


class InputCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assetId: uuid.UUID


class StyleInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    positivePrompt: str = Field(min_length=1, max_length=20000)
    negativePrompt: str = Field(default="", max_length=20000)


class StyleDuplicate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ImagePreferencesInput(BaseModel):
    width: int = Field(ge=64, le=4096, multiple_of=8)
    height: int = Field(ge=64, le=4096, multiple_of=8)
    steps: int = Field(ge=1, le=150)
    cfg: float = Field(ge=0, le=100, allow_inf_nan=False)


def style_view(style: ImageStyle):
    return {"id": str(style.id), "name": style.name,
            "positivePrompt": style.positive_prompt, "negativePrompt": style.negative_prompt,
            "createdAt": style.created_at.isoformat(), "updatedAt": style.updated_at.isoformat()}


def save_style(db: Session, style: ImageStyle):
    style.name = style.name.strip()
    if not style.name:
        raise HTTPException(422, "Style name is required")
    db.add(style)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Style name is already in use") from None
    return style_view(style)


def asset_view(asset: Asset) -> dict:
    prefix = f"/api/executions/{asset.execution_id}/assets/{asset.id}"
    return {"id": str(asset.id), "executionId": str(asset.execution_id),
            "displayName": asset.display_name, "mimeType": asset.mime_type,
            "mediaKind": asset.media_kind, "sizeBytes": asset.size_bytes,
            "source": asset.source, "previewKind": preview_kind(asset.media_kind, asset.mime_type),
            "origin": "external" if (asset.extra_metadata or {}).get("external_import") is True else "studio",
            "outputRole": output_role((asset.extra_metadata or {}).get("output_role")),
            "width": asset.width, "height": asset.height,
            "createdAt": asset.created_at.isoformat(),
            "hasThumbnail": asset.thumbnail_locator is not None,
            "previewUrl": f"{prefix}/content", "thumbnailUrl": f"{prefix}/thumbnail",
            "downloadUrl": f"{prefix}/download"}


def view(execution: Execution, db: Session):
    assets = db.scalars(select(Asset).where(Asset.execution_id == execution.id,
                                            Asset.user_id == execution.user_id,
                                            Asset.availability != "deleted").order_by(Asset.created_at)).all()
    return {"id": str(execution.id), "state": execution.last_known_status,
            "source": execution.source, "category": execution.category, "operation": execution.operation,
            "submittedAt": execution.submitted_at.isoformat(),
            "assets": [asset_view(asset) for asset in assets],
            **({"warnings": ["abc_unavailable"]} if execution.operation == "music.transcribe" and execution.last_known_status == "completed" and any(a.mime_type == "audio/midi" for a in assets) and not any(a.mime_type == "text/vnd.abc" for a in assets) else {})}


def owned(db: Session, execution_id: uuid.UUID, user_id: uuid.UUID) -> Execution:
    execution = db.scalar(select(Execution).where(Execution.id == execution_id, Execution.user_id == user_id))
    if execution is None:
        raise HTTPException(404)
    return execution


def owned_asset(db: Session, execution_id: uuid.UUID, asset_id: uuid.UUID, user_id: uuid.UUID) -> Asset:
    execution = owned(db, execution_id, user_id)
    if execution.source != "generation":
        raise HTTPException(404)
    asset = db.scalar(select(Asset).where(Asset.id == asset_id, Asset.execution_id == execution_id,
                                         Asset.user_id == user_id, Asset.availability != "deleted"))
    if asset is None or asset.source != "generation":
        raise HTTPException(404)
    return asset


def set_status(db: Session, execution: Execution, status: str):
    execution.last_known_status = status
    execution.updated_at = now()
    if status == "running" and execution.started_at is None:
        execution.started_at = now()
    if status in {"completed", "failed", "cancelled"} and execution.completed_at is None:
        execution.completed_at = now()
    db.commit()


def create_app(session_factory=None, gateway=None, thumbnails=None):
    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(maintenance(app))
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(title="FLAMORIS Studio", lifespan=lifespan)
    app.state.session_factory = session_factory or make_session_factory()
    app.state.gateway = gateway or GenerationGateway()
    app.state.thumbnails = thumbnails or Thumbnails(os.getenv("STUDIO_THUMBNAIL_DIR", ""))
    root = app.state.thumbnails.root
    app.state.input_thumbnails = InputThumbnails(str(root / "inputs") if root else "")
    app.state.input_limits = Limits.from_env()
    mount_input_uploads(app)
    app.state.download_slots = asyncio.Semaphore(2)
    app.state.preview_admission = PreviewAdmission(app.state.download_slots)
    app.state.prepare_slots = asyncio.Semaphore(1)
    mount_assistant(app)
    mount_intelligence(app)
    mount_external_import(app, view)
    mount_speech(app, view, set_status)
    mount_music(app, view, set_status)

    @app.exception_handler(GatewayError)
    async def gateway_error(_, exc: GatewayError):
        code = exc.code if exc.code in {"busy", "unavailable", "validation", "upstream_failure", "asset_too_large", "transfer_unavailable"} else "upstream_failure"
        return JSONResponse(status_code={"busy": 409, "unavailable": 503, "validation": 422,
                                         "upstream_failure": 502, "asset_too_large": 413, "transfer_unavailable": 503}[code],
                            content={"error": code, "message": {"busy": "Generation service is busy.",
                                "unavailable": "Generation service is unavailable.",
                                "validation": "Invalid generation result.",
                                "upstream_failure": "Generation service failed.",
                                "asset_too_large": "This asset exceeds the current download limit. Its gallery entry is preserved.",
                                "transfer_unavailable": "Asset transfer is unavailable. The service deployment is incomplete."}[code]})

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            try:
                require_csrf(request)
            except HTTPException:
                return JSONResponse({"error": "Invalid request token."}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith(("/api/assistant/", "/api/intelligence/", "/api/generation/inputs", "/api/generation/speech/", "/api/generation/music/", "/api/generation/external-import")):
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/api/session")
    def session(request: Request, response: Response, db: Session = Depends(database)):
        user = None
        token = request.cookies.get(COOKIE)
        if token:
            login = db.get(LoginSession, digest(token))
            if login and login.expires_at > now():
                user = db.get(User, login.user_id)
        csrf = new_csrf(response, request.cookies.get(CSRF_COOKIE))
        response.headers["Cache-Control"] = "no-store"
        return {"authenticated": user is not None, "userName": user.email if user else None,
                "accountKey": digest(f"speech-owner:{user.id}") if user else None,
                "csrfToken": csrf, "allowRegistration": os.getenv("STUDIO_ALLOW_REGISTRATION") == "1"}

    @app.post("/api/auth/register")
    def register(input: Credentials, response: Response, db: Session = Depends(database)):
        if os.getenv("STUDIO_ALLOW_REGISTRATION") != "1":
            raise HTTPException(404)
        email = normalized_email(input.email)
        user = User(email=email, password_hash=hasher.hash(input.password))
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(400, "Unable to create account") from None
        start_session(response, db, user.id)
        return {"userName": user.email}

    @app.post("/api/auth/login")
    def login(input: Credentials, request: Request, response: Response, db: Session = Depends(database)):
        address = request.client.host if request.client else "unknown"
        admit_login(db, address)
        # Serialize per-account failure counters across workers while performing
        # exactly one verification for known, unknown and locked accounts.
        user = db.scalar(select(User).where(User.email == normalized_email(input.email)).with_for_update())
        locked = bool(user and user.locked_until and user.locked_until > now())
        try:
            verified = hasher.verify(user.password_hash if user else DUMMY_PASSWORD_HASH, input.password)
            valid = bool(user) and verified and not locked
        except VerificationError:
            valid = False
        if not valid:
            if user and not locked:
                user.failed_logins += 1
                if user.failed_logins >= 5:
                    user.locked_until = now() + timedelta(minutes=15)
                    user.failed_logins = 0
                db.commit()
            raise HTTPException(401)
        user.failed_logins = 0
        user.locked_until = None
        db.commit()
        prior = request.cookies.get(COOKIE)
        if prior:
            old = db.get(LoginSession, digest(prior))
            if old:
                db.delete(old)
                db.commit()
        start_session(response, db, user.id)
        return {"userName": user.email}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response, db: Session = Depends(database),
               _: uuid.UUID = Depends(authenticated_user)):
        clear_session(response, db, request.cookies.get(COOKIE, ""))
        return {"ok": True}

    @app.post("/api/account/email")
    def change_email(input: EmailChange, db: Session = Depends(database),
                     user_id: uuid.UUID = Depends(authenticated_user)):
        user = db.get(User, user_id)
        try:
            valid = hasher.verify(user.password_hash, input.currentPassword)
        except VerificationError:
            valid = False
        if not valid:
            raise HTTPException(403, "Current password is incorrect")
        email = normalized_email(input.email)
        if email == user.email:
            return {"userName": user.email}
        user.email = email
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Email address is already in use") from None
        return {"userName": user.email}

    @app.post("/api/account/password")
    def change_password(input: PasswordChange, request: Request, db: Session = Depends(database),
                        user_id: uuid.UUID = Depends(authenticated_user)):
        if input.newPassword != input.confirmPassword:
            raise HTTPException(422, "New passwords do not match")
        # Serialize account password changes and revoke other sessions in the
        # same transaction. The current opaque session token remains unchanged.
        user = db.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None:
            raise HTTPException(401)
        try:
            valid = hasher.verify(user.password_hash, input.currentPassword)
        except VerificationError:
            valid = False
        if not valid:
            raise HTTPException(403, "Current password is incorrect")
        token_hash = digest(request.cookies.get(COOKIE, ""))
        # current_user loaded this row earlier. Explicit SELECT still takes a
        # database lock, so concurrent logout cannot remove it before commit.
        active = db.scalar(select(LoginSession).where(LoginSession.token_hash == token_hash).with_for_update())
        if active is None or active.user_id != user_id or active.expires_at <= now():
            raise HTTPException(401)
        try:
            user.password_hash = hasher.hash(input.newPassword)
            db.execute(delete(LoginSession).where(LoginSession.user_id == user_id,
                                                 LoginSession.token_hash != token_hash))
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            raise HTTPException(503, "Password change could not be saved") from None
        return {"ok": True}

    @app.get("/api/system/status")
    def status():
        return {"healthy": True, "service": "studio"}

    @app.get("/api/generation/image/discovery")
    async def discovery(_: uuid.UUID = Depends(authenticated_user)):
        return await app.state.gateway.discover()

    @app.get("/api/generation/image/preferences")
    def get_image_preferences(db: Session = Depends(database), user_id: uuid.UUID = Depends(authenticated_user)):
        preference = db.get(ImagePreference, user_id)
        if preference is None:
            return {"width": 512, "height": 512, "steps": 20, "cfg": 7}
        return {key: getattr(preference, key) for key in ("width", "height", "steps", "cfg")}

    @app.put("/api/generation/image/preferences")
    def put_image_preferences(input: ImagePreferencesInput, db: Session = Depends(database),
                              user_id: uuid.UUID = Depends(authenticated_user)):
        preference = db.get(ImagePreference, user_id)
        if preference is None:
            preference = ImagePreference(owner_user_id=user_id)
        for key, value in input.model_dump().items():
            setattr(preference, key, value)
        preference.updated_at = now()
        db.add(preference)
        db.commit()
        return input.model_dump()

    @app.get("/api/generation/image/styles")
    def list_styles(db: Session = Depends(database), user_id: uuid.UUID = Depends(authenticated_user)):
        return {"items": [style_view(style) for style in db.scalars(select(ImageStyle)
                .where(ImageStyle.owner_user_id == user_id).order_by(ImageStyle.name, ImageStyle.id)).all()]}

    @app.post("/api/generation/image/styles", status_code=201)
    def create_style(input: StyleInput, db: Session = Depends(database),
                     user_id: uuid.UUID = Depends(authenticated_user)):
        return save_style(db, ImageStyle(owner_user_id=user_id, name=input.name,
                          positive_prompt=input.positivePrompt, negative_prompt=input.negativePrompt))

    def owned_style(db: Session, style_id: uuid.UUID, user_id: uuid.UUID):
        style = db.scalar(select(ImageStyle).where(ImageStyle.id == style_id,
                          ImageStyle.owner_user_id == user_id))
        if style is None:
            raise HTTPException(404)
        return style

    @app.get("/api/generation/image/styles/{style_id}")
    def get_style(style_id: uuid.UUID, db: Session = Depends(database),
                  user_id: uuid.UUID = Depends(authenticated_user)):
        return style_view(owned_style(db, style_id, user_id))

    @app.put("/api/generation/image/styles/{style_id}")
    def update_style(style_id: uuid.UUID, input: StyleInput, db: Session = Depends(database),
                     user_id: uuid.UUID = Depends(authenticated_user)):
        style = owned_style(db, style_id, user_id)
        style.name, style.positive_prompt, style.negative_prompt = input.name, input.positivePrompt, input.negativePrompt
        style.updated_at = now()
        return save_style(db, style)

    @app.post("/api/generation/image/styles/{style_id}/duplicate", status_code=201)
    def duplicate_style(style_id: uuid.UUID, input: StyleDuplicate, db: Session = Depends(database),
                        user_id: uuid.UUID = Depends(authenticated_user)):
        source = owned_style(db, style_id, user_id)
        return save_style(db, ImageStyle(owner_user_id=user_id, name=input.name,
                          positive_prompt=source.positive_prompt, negative_prompt=source.negative_prompt,
                          recommended_model=source.recommended_model,
                          recommended_loras=source.recommended_loras,
                          recommended_parameters=source.recommended_parameters))

    @app.delete("/api/generation/image/styles/{style_id}")
    def delete_style(style_id: uuid.UUID, db: Session = Depends(database),
                     user_id: uuid.UUID = Depends(authenticated_user)):
        db.delete(owned_style(db, style_id, user_id))
        db.commit()
        return {"ok": True}

    @app.post("/api/generation/inputs", status_code=201)
    async def new_input(body: InputCreate, request: Request, db: Session = Depends(database),
                        user_id: uuid.UUID = Depends(authenticated_user)):
        asset = db.scalar(select(Asset).where(Asset.id == body.assetId, Asset.user_id == user_id,
                                             Asset.availability != "deleted"))
        if asset is None:
            raise HTTPException(404)
        owned(db, asset.execution_id, user_id)
        if (asset.mime_type, asset.media_kind) not in {("image/png", "image"), ("image/jpeg", "image"), ("image/webp", "image"), ("audio/wav", "audio")} or (asset.mime_type == "audio/wav" and os.getenv("STUDIO_MUSIC_ENABLED", "false").lower() != "true"):
            raise HTTPException(422, "Unsupported reference Asset")
        row = reserve(db, user_id, asset, app.state.input_limits)
        async def source_bytes():
            current = owned_asset(db, asset.execution_id, asset.id, user_id)
            return await get_content(current, db, request, user_id)
        result = await create_snapshot(app, db, row, source_bytes)
        db.rollback()
        if current_user(request, db) != user_id:
            raise HTTPException(401)
        owned_input(db, row.id, user_id)
        return result

    @app.get("/api/generation/inputs/{input_id}")
    async def get_input(input_id: uuid.UUID, db: Session = Depends(database),
                        user_id: uuid.UUID = Depends(authenticated_user)):
        row = owned_input(db, input_id, user_id)
        if usable(row):
            try:
                await check_input(app.state.gateway, row)
            except GatewayError:
                return {**input_view(row), "available": False}
        return input_view(row)

    @app.delete("/api/generation/inputs/{input_id}")
    async def delete_input(input_id: uuid.UUID, db: Session = Depends(database),
                           user_id: uuid.UUID = Depends(authenticated_user)):
        row = owned_input(db, input_id, user_id)
        quota_guard(db)
        if protected(db, row.id):
            raise HTTPException(409, "Reference image is in use")
        row.state = "revoking"
        db.commit()
        try:
            if row.upstream_input_id:
                result = await app.state.gateway.delete_input(row.upstream_input_id)
                if result.get("deleted") is not True:
                    raise GatewayError("upstream_failure")
            row.state = "revoked"
            row.terminal_at = min(row.expires_at, now())
            db.commit()
        except GatewayError:
            # The delete may have committed upstream. Keep the input unavailable
            # until an explicit delete retry or TTL reconciliation confirms it.
            raise HTTPException(409, "Reference deletion could not be confirmed; retry after reconciliation") from None
        return input_view(row)

    @app.get("/api/generation/inputs/{input_id}/thumbnail")
    def input_thumbnail(input_id: uuid.UUID, db: Session = Depends(database),
                        user_id: uuid.UUID = Depends(authenticated_user)):
        row = owned_input(db, input_id, user_id)
        terminal = min(row.expires_at, row.terminal_at) if row.terminal_at else row.expires_at
        if terminal + timedelta(hours=24) <= now():
            raise HTTPException(404)
        data = app.state.input_thumbnails.load(row.thumbnail_locator) if row.thumbnail_locator else None
        if data is None:
            raise HTTPException(404)
        return Response(data, media_type="image/webp", headers={"Cache-Control": "private, no-store"})

    def image_route():
        return tuple(os.getenv(key, "") for key in (
            "STUDIO_GENERATION_ENDPOINT", "STUDIO_GENERATION_TOKEN", "STUDIO_GENERATION_NAMESPACE"))

    def image_access(request, db, user_id, route):
        db.rollback()
        if current_user(request, db) != user_id:
            raise HTTPException(401)
        if image_route() != route:
            raise HTTPException(409, "Image configuration changed; refresh before submitting")

    def accepted_image_job(job):
        if (type(job) is not dict or type(job.get("job_id")) is not str
            or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job["job_id"]) is None
            or type(job.get("status")) is not str
            or job["status"] not in {"queued", "running", "completed", "failed", "cancelled", "unknown"}):
            raise GatewayError("upstream_failure")
        return job

    async def selected_submit(input, request, db, user_id, route):
        discovery = await app.state.gateway.discover()
        image_access(request, db, user_id, route)
        selected = next((item for item in discovery.get("workflows", []) if item["id"] == input.workflowId), None)
        if (not discovery["available"] or not selected or not selected["selectable"]
            or selected["kind"] != "builtin" or selected["kind"] != input.workflowKind
            or selected["id"] not in {"text-to-image", "text-to-image-lora"}
            or selected["image"].get("mode") != "txt2img"):
            raise HTTPException(409, "Image template unavailable; select a ready template")
        values = input.model_dump(mode="json", exclude_none=True)
        try:
            parameters = map_parameters(selected, values)
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, "Image parameters are unsupported") from exc
        for role in ("steps", "cfg", "seed", "sampler", "scheduler", "denoise"):
            values.pop(ROLES[role], None)
        for key, spec in selected["parameters"].items():
            role = spec.get("role")
            if role in ROLES and role != "loras" and key in parameters:
                values[ROLES[role]] = parameters[key]
        snapshot = {**values, "snapshotVersion": 2, "normalizedParameters": parameters}
        execution = Execution(user_id=user_id, workflow=selected["id"], request_snapshot=snapshot)
        db.add(execution)
        db.commit()
        try:
            workflow = await app.state.gateway.build_selected(selected, parameters)
            image_access(request, db, user_id, route)
        except (GatewayError, HTTPException):
            set_status(db, execution, "failed")
            raise
        try:
            job = accepted_image_job(await app.state.gateway.submit(workflow))
            execution.upstream_job_id = job["job_id"]
            set_status(db, execution, job["status"])
        except GatewayError as exc:
            set_status(db, execution, "busy" if exc.code == "busy" else "submission_unknown")
            image_access(request, db, user_id, route)
            raise
        image_access(request, db, user_id, route)
        return view(execution, db)

    @app.post("/api/generation/image/jobs", status_code=201)
    async def submit(input: WorkflowImageRequest | ImageRequest, request: Request,
                     db: Session = Depends(database), user_id: uuid.UUID = Depends(authenticated_user)):
        route = image_route()
        if isinstance(input, WorkflowImageRequest):
            return await selected_submit(input, request, db, user_id, route)
        capability = await app.state.gateway.discover()
        image_access(request, db, user_id, route)
        template = "text-to-image-lora" if input.loras else "text-to-image"
        if not capability["available"] or template not in capability["templates"]:
            raise GatewayError("unavailable")
        if input.seed is None:
            input.seed = secrets.randbelow(MAX_SAFE_IMAGE_SEED + 1)
        workflow = await app.state.gateway.build(template, input.parameters())
        image_access(request, db, user_id, route)
        execution = Execution(user_id=user_id, workflow=template, request_snapshot=input.model_dump())
        db.add(execution)
        db.commit()  # Persist uncertain submissions before calling the non-idempotent upstream tool.
        try:
            job = accepted_image_job(await app.state.gateway.submit(workflow))
            execution.upstream_job_id = job["job_id"]
            set_status(db, execution, job["status"])
        except GatewayError as exc:
            set_status(db, execution, "busy" if exc.code == "busy" else "submission_unknown")
            image_access(request, db, user_id, route)
            raise
        image_access(request, db, user_id, route)
        return view(execution, db)

    @app.get("/api/assets")
    def list_generated_assets(limit: int = Query(default=24, ge=1, le=48),
                              offset: int = Query(default=0, ge=0, le=100000),
                              db: Session = Depends(database),
                              user_id: uuid.UUID = Depends(authenticated_user)):
        rows = db.execute(select(Asset, Execution).join(Execution, Asset.execution_id == Execution.id)
                          .where(Asset.user_id == user_id, Execution.user_id == user_id,
                                 Asset.availability != "deleted")
                          .order_by(Asset.created_at.desc(), Asset.id.desc())
                          .offset(offset).limit(limit + 1)).all()
        return {"items": [asset_view(asset) for asset, _ in rows[:limit]],
                "nextOffset": offset + limit if len(rows) > limit else None}

    @app.get("/api/assets/{asset_id}")
    async def generated_asset_detail(asset_id: uuid.UUID, db: Session = Depends(database),
                               user_id: uuid.UUID = Depends(authenticated_user)):
        row = db.execute(select(Asset, Execution).join(Execution, Asset.execution_id == Execution.id)
                         .where(Asset.id == asset_id, Asset.user_id == user_id,
                                Execution.user_id == user_id, Asset.availability != "deleted")).first()
        if row is None:
            raise HTTPException(404)
        asset, execution = row
        request = execution.request_snapshot if isinstance(execution.request_snapshot, dict) else {}
        settings = {key: request[key] for key in ("positivePrompt", "negativePrompt", "checkpoint",
                    "seed", "steps", "cfg", "width", "height", "loras", "sampler", "scheduler", "denoise", "workflowId", "workflowKind", "definitionVersion", "definitionDigest",
                    "referenceInputId", "additionalParameters") if key in request}
        reference = None
        if settings.get("referenceInputId"):
            try:
                handle = uuid.UUID(settings["referenceInputId"])
                mapping = owned_input(db, handle, user_id)
                reference = input_view(mapping)
                if usable(mapping):
                    try:
                        await check_input(app.state.gateway, mapping)
                    except (GatewayError, HTTPException):
                        reference["available"] = False
            except (ValueError, HTTPException):
                reference = {"id": settings["referenceInputId"], "available": False, "thumbnailUrl": None}
        workflow_available = True
        if settings.get("workflowId"):
            try:
                catalog = await app.state.gateway.discover()
                item = next((d for d in catalog.get("workflows", []) if d["id"] == settings["workflowId"]), None)
                workflow_available = bool(item and item["selectable"] and item["definitionVersion"] == settings.get("definitionVersion") and item["definitionDigest"] == settings.get("definitionDigest"))
            except GatewayError:
                workflow_available = False
        return {**asset_view(asset), "state": execution.last_known_status,
                "submittedAt": execution.submitted_at.isoformat(), "settings": settings,
                "referenceInput": reference, "workflowAvailable": workflow_available}

    @app.post("/api/assets/delete")
    async def delete_generated_assets(input: DeleteAssetsRequest, db: Session = Depends(database),
                                      user_id: uuid.UUID = Depends(authenticated_user)):
        def cleanup_thumbnail(asset: Asset) -> None:
            # Preserve the locator until cleanup succeeds so a retry can finish it.
            if asset.thumbnail_locator is None:
                return
            try:
                app.state.thumbnails.delete(asset.thumbnail_locator)
            except OSError:
                return
            asset.thumbnail_locator = None
            db.commit()

        results = []
        for asset_id in dict.fromkeys(input.ids):
            # Serialize with result catalog updates, including a stale assets.list response.
            row = db.execute(select(Asset, Execution).join(Execution, Asset.execution_id == Execution.id)
                             .where(Asset.id == asset_id, Asset.user_id == user_id,
                                    Execution.user_id == user_id, Asset.source == "generation",
                                    Execution.source == "generation")
                             .with_for_update(of=Execution)).first()
            if row is None:
                results.append({"id": str(asset_id), "deleted": False, "error": "not_found"})
                db.rollback()
                continue
            asset, _ = row
            if asset.availability == "deleted":
                cleanup_thumbnail(asset)
                results.append({"id": str(asset_id), "deleted": True})
                db.rollback()
                continue
            try:
                upstream = await app.state.gateway.delete_asset(asset.upstream_asset_id)
                if upstream.get("deleted") is not True:
                    raise GatewayError("upstream_failure")
                asset.availability = "deleted"
                asset.updated_at = now()
                db.commit()
                cleanup_thumbnail(asset)
                results.append({"id": str(asset_id), "deleted": True})
            except GatewayError as exc:
                db.rollback()
                results.append({"id": str(asset_id), "deleted": False, "error": exc.code})
        return {"results": results}

    @app.get("/api/executions/{execution_id}")
    async def execution_status(execution_id: uuid.UUID, db: Session = Depends(database),
                               user_id: uuid.UUID = Depends(authenticated_user)):
        execution = owned(db, execution_id, user_id)
        if execution.source != "generation":
            raise HTTPException(404)
        if execution.upstream_job_id and execution.last_known_status not in {"completed", "failed", "cancelled"}:
            job = await app.state.gateway.status(execution.upstream_job_id)
            set_status(db, execution, job["status"])
        return view(execution, db)

    @app.get("/api/executions/{execution_id}/result")
    async def result(execution_id: uuid.UUID, db: Session = Depends(database),
                     user_id: uuid.UUID = Depends(authenticated_user)):
        execution = owned(db, execution_id, user_id)
        if execution.source != "generation":
            raise HTTPException(404)
        if not execution.upstream_job_id:
            return view(execution, db)
        if execution.last_known_status != "completed":
            job = await app.state.gateway.status(execution.upstream_job_id)
            set_status(db, execution, job["status"])
            if job["status"] != "completed":
                return view(execution, db)
        if execution.upstream_job_id and db.get(ExternalImport, execution.upstream_job_id) is not None:
            # Imported completed outputs are an immutable catalog snapshot. An
            # upstream listing cannot silently add unverified assets later.
            return view(execution, db)
        # Catalog metadata is independent of full materialization and thumbnails.
        # Completed status is already persisted, so retry listing after a timeout
        # (or Studio restart) without requiring the old live provider job mapping.
        try:
            listing = await app.state.gateway.assets(execution.upstream_job_id)
        except GatewayError:
            cached = view(execution, db)
            if cached["assets"]:
                return {**cached, "catalogSync": "unavailable"}
            raise
        outputs = normalize_outputs(listing)
        if execution.category == "speech" and (
                len(outputs) != 1 or outputs[0].media_kind != "audio" or outputs[0].mime_type != "audio/wav" or
                outputs[0].role != {"port": "audio", "role": "audio", "index": 0}):
            raise GatewayError("validation")
        if execution.category == "music":
            checked_music_outputs(outputs, execution.operation, execution.request_snapshot)
        catalog_guard(db)
        # Lock the parent row to serialize concurrent catalog updates across processes.
        db.execute(text("SELECT id FROM executions WHERE id = :id FOR UPDATE"), {"id": execution.id})
        known = {asset.upstream_asset_id: asset for asset in db.scalars(
            select(Asset).where(Asset.execution_id == execution.id, Asset.user_id == user_id))}
        for item in outputs:
            upstream, size = item.upstream_id, item.size_bytes
            claim = db.get(ExternalAssetClaim, upstream)
            if claim is not None and claim.user_id != user_id:
                raise GatewayError("validation")
            if upstream in known:
                existing = known[upstream]
                if existing.availability == "deleted":
                    continue
                prior_role = output_role((existing.extra_metadata or {}).get("output_role"))
                if ((existing.media_kind, existing.mime_type) != (item.media_kind, item.mime_type) or
                    prior_role is not None and prior_role != item.role):
                    raise GatewayError("validation")
                if item.role is not None:
                    existing.extra_metadata = {**(existing.extra_metadata or {}), "output_role": item.role}
                if size is not None:
                    existing.size_bytes = size
                continue
            asset_id = uuid.uuid4()
            asset = Asset(id=asset_id, user_id=user_id, execution_id=execution.id, upstream_asset_id=upstream,
                         storage_locator=upstream, original_filename=item.display_name,
                         display_name=generated_filename(asset_id, execution.submitted_at,
                             len(known) + 1, item.media_kind, item.mime_type),
                         media_kind=item.media_kind, mime_type=item.mime_type,
                         size_bytes=size, extra_metadata={"output_role": item.role} if item.role else {})
            db.add(asset)
            known[upstream] = asset
        db.commit()
        return view(execution, db)

    @app.post("/api/executions/{execution_id}/cancel")
    async def cancel(execution_id: uuid.UUID, db: Session = Depends(database),
                     user_id: uuid.UUID = Depends(authenticated_user)):
        execution = owned(db, execution_id, user_id)
        if execution.source != "generation":
            raise HTTPException(404)
        if execution.upstream_job_id and execution.last_known_status not in {"completed", "failed", "cancelled"}:
            job = await app.state.gateway.cancel(execution.upstream_job_id)
            set_status(db, execution, job["status"])
        return view(execution, db)

    async def prepare_owned_asset(asset: Asset, db: Session, request: Request, user_id: uuid.UUID):
        # Generation admits just one prepare at a time, even for materialized
        # assets. Serialize this stage across previews and downloads, while reads
        # still share two transfer slots. Never hold a DB transaction while queued.
        execution_id, asset_id, upstream_id = asset.execution_id, asset.id, asset.upstream_asset_id
        db.rollback()
        slots = app.state.prepare_slots
        try:
            await asyncio.wait_for(slots.acquire(), timeout=30)
        except TimeoutError:
            raise HTTPException(429, "Asset preparation is busy", headers={"Retry-After": "2"}) from None
        try:
            if await request.is_disconnected():
                raise asyncio.CancelledError()
            if current_user(request, db) != user_id:
                raise HTTPException(401)
            owned_asset(db, execution_id, asset_id, user_id)
            return await app.state.gateway.prepare_asset(upstream_id)
        finally:
            slots.release()

    async def get_content(asset: Asset, db: Session, request: Request, user_id: uuid.UUID):
        max_bytes = min(max(int(os.getenv("STUDIO_MAX_ASSET_BYTES", "67108864")), 1), 67108864)
        if asset.size_bytes and asset.size_bytes > max_bytes:
            raise GatewayError("asset_too_large")
        if asset.size_bytes is not None and asset.size_bytes <= NATIVE_IMAGE_BYTES:
            data, mime = await app.state.gateway.content(asset.upstream_asset_id, max_bytes)
        else:
            # Release the row lock before remote I/O. An owner/deletion check is
            # repeated at every read, including retries.
            db.rollback()
            async with app.state.preview_admission.acquire():
                if await request.is_disconnected():
                    raise asyncio.CancelledError()
                if current_user(request, db) != user_id:
                    raise HTTPException(401)
                owned_asset(db, asset.execution_id, asset.id, user_id)
                prepared = await prepare_owned_asset(asset, db, request, user_id)
                size, expected, limit = metadata(prepared, asset.upstream_asset_id, asset.mime_type, max_bytes)
                parts = bytearray()
                checksum = hashlib.sha256()
                while len(parts) < size:
                    offset = len(parts)
                    async def attempt():
                        if await request.is_disconnected():
                            raise asyncio.CancelledError()
                        if current_user(request, db) != user_id:
                            raise HTTPException(401)
                        owned_asset(db, asset.execution_id, asset.id, user_id)
                        result = await app.state.gateway.read_asset(asset.upstream_asset_id, expected, offset, limit)
                        return chunk(result, asset.upstream_asset_id, expected, offset, size, limit)
                    part = await read_with_retry(attempt)
                    checksum.update(part)
                    parts.extend(part)
                if checksum.hexdigest() != expected:
                    raise GatewayError("validation")
                data, mime = bytes(parts), asset.mime_type
        if mime != asset.mime_type or len(data) > max_bytes:
            raise GatewayError("validation")
        try:
            asset.width, asset.height = inspect_image(data, mime)
        except ValueError as exc:
            raise GatewayError("validation") from exc
        asset.size_bytes = len(data)
        db.commit()
        return data, mime

    @app.get("/api/executions/{execution_id}/assets/{asset_id}/thumbnail")
    async def thumbnail(execution_id: uuid.UUID, asset_id: uuid.UUID, request: Request, db: Session = Depends(database),
                        user_id: uuid.UUID = Depends(authenticated_user)):
        owned(db, execution_id, user_id)
        db.execute(text("SELECT id FROM executions WHERE id = :id FOR UPDATE"), {"id": execution_id})
        asset = owned_asset(db, execution_id, asset_id, user_id)
        if preview_kind(asset.media_kind, asset.mime_type) != "image":
            raise HTTPException(404)
        if asset.thumbnail_locator is None:
            data, _ = await get_content(asset, db, request, user_id)
            try:
                asset.thumbnail_locator = app.state.thumbnails.save(asset.id, data)
                db.commit()
            except (OSError, ValueError):
                raise HTTPException(503, "Thumbnail is temporarily unavailable") from None
        data = app.state.thumbnails.load(asset.thumbnail_locator) if asset.thumbnail_locator else None
        if data is None:
            raise HTTPException(404)
        return Response(data, media_type="image/webp", headers={"Cache-Control": "private, no-store",
                         "X-Content-Type-Options": "nosniff"})

    @app.get("/api/executions/{execution_id}/assets/{asset_id}/content")
    @app.get("/api/executions/{execution_id}/assets/{asset_id}/download")
    async def asset_content(execution_id: uuid.UUID, asset_id: uuid.UUID, request: Request,
                            db: Session = Depends(database), user_id: uuid.UUID = Depends(authenticated_user)):
        owned(db, execution_id, user_id)
        db.execute(text("SELECT id FROM executions WHERE id = :id FOR UPDATE"), {"id": execution_id})
        asset = owned_asset(db, execution_id, asset_id, user_id)
        if asset.source != "generation":
            raise HTTPException(404)
        kind = preview_kind(asset.media_kind, asset.mime_type)
        if kind != "image":
            # Non-image content always uses the prepared bounded transport; no
            # image/base64 fallback or arbitrary browser-executable preview.
            upstream_id, mime, display_name = asset.upstream_asset_id, asset.mime_type, asset.display_name
            db.rollback()
            cap = min(max(int(os.getenv("STUDIO_MAX_TRANSFER_BYTES", str(MAX_TRANSFER_BYTES))), 1), MAX_TRANSFER_BYTES)

            async def authorize():
                if await request.is_disconnected():
                    raise asyncio.CancelledError()
                db.rollback()
                if current_user(request, db) != user_id:
                    raise HTTPException(401)
                current = owned_asset(db, execution_id, asset_id, user_id)
                if (current.source != "generation" or current.mime_type != mime or
                    current.upstream_asset_id != upstream_id):
                    raise HTTPException(404)

            async def prepare():
                await authorize()
                return await prepare_owned_asset(asset, db, request, user_id)

            return await representation_response(gateway=app.state.gateway, prepare=prepare,
                authorize=authorize, slots=app.state.download_slots, upstream_id=upstream_id,
                mime=mime, display_name=display_name, max_bytes=cap,
                range_headers=request.headers.getlist("range"),
                if_range=(request.headers.get("if-range")
                          if len(request.headers.getlist("if-range")) <= 1 else "unsupported"),
                allow_range=kind in {"audio", "video"},
                attachment=kind == "file" or request.url.path.endswith("/download"))
        inline_limit = min(max(int(os.getenv("STUDIO_MAX_ASSET_BYTES", "67108864")), 1), 67108864)
        if request.url.path.endswith("/download") and (asset.size_bytes is None or asset.size_bytes > min(inline_limit, NATIVE_IMAGE_BYTES)):
            # No row lock across remote I/O; every chunk checks ownership and deletion.
            db.rollback()
            cap = min(max(int(os.getenv("STUDIO_MAX_TRANSFER_BYTES", str(MAX_TRANSFER_BYTES))), 1),
                      MAX_TRANSFER_BYTES)
            slots = app.state.download_slots
            try:
                await asyncio.wait_for(slots.acquire(), timeout=0.01)
            except TimeoutError:
                raise HTTPException(429, "Too many active downloads") from None
            released = False

            def release():
                nonlocal released
                if not released:
                    released = True
                    slots.release()

            try:
                owned_asset(db, execution_id, asset_id, user_id)
                prepared = await prepare_owned_asset(asset, db, request, user_id)
                size, digest, limit = metadata(prepared, asset.upstream_asset_id, asset.mime_type, cap)
                async def read_at(offset):
                    async def attempt():
                        if await request.is_disconnected():
                            raise asyncio.CancelledError()
                        if current_user(request, db) != user_id:
                            raise HTTPException(401)
                        owned_asset(db, execution_id, asset_id, user_id)
                        result = await app.state.gateway.read_asset(asset.upstream_asset_id, digest, offset, limit)
                        return chunk(result, asset.upstream_asset_id, digest, offset, size, limit)
                    return await read_with_retry(attempt)
                first = await read_at(0)
            except BaseException:
                release()
                raise

            async def stream():
                hasher = hashlib.sha256()
                offset = 0
                data = first
                try:
                    while True:
                        hasher.update(data)
                        offset += len(data)
                        if offset == size and hasher.hexdigest() != digest:
                            raise GatewayError("validation")
                        yield data
                        if offset == size:
                            break
                        data = await read_at(offset)
                finally:
                    release()
            return OwnedStreamingResponse(stream(), release=release, media_type=asset.mime_type, headers={
                "Content-Length": str(size), "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "Content-Disposition": f'attachment; filename="{filename(asset.display_name)}"'})
        data, mime = await get_content(asset, db, request, user_id)
        headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                   "Content-Security-Policy": "default-src 'none'; sandbox"}
        if request.url.path.endswith("/download"):
            headers["Content-Disposition"] = f'attachment; filename="{filename(asset.display_name)}"'
        return Response(data, media_type=mime, headers=headers)

    dist = os.getenv("STUDIO_WEB_DIST")
    if dist and os.path.isdir(dist):
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


def production_app():
    configure_logging().info("Studio process starting")
    return create_app()
