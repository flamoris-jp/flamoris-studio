"""Studio HTTP boundary: all user-owned lookups include the authenticated owner."""
import os
import secrets
import asyncio
import hashlib
import uuid
import time
from collections import defaultdict, deque
from datetime import timedelta

from argon2.exceptions import VerificationError
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from .auth import COOKIE, CSRF_COOKIE, clear_session, current_user, database, digest, hasher, new_csrf, require_csrf, start_session
from .db import Asset, Execution, ImagePreference, ImageStyle, LoginSession, User, make_session_factory, now
from .gateway import GatewayError, GenerationGateway
from .media import Thumbnails, filename, inspect_image
from .logging_setup import configure_logging
from .transfer import CHUNK_BYTES, MAX_TRANSFER_BYTES, PreviewAdmission, chunk, metadata, read_with_retry

# 512 KiB of raw image data stays below a 1 MiB SSE event even after base64
# encoding and the MCP JSON envelope. Unknown sizes take the bounded route.
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
            "submittedAt": execution.submitted_at.isoformat(),
            "assets": [asset_view(asset) for asset in assets]}


def owned(db: Session, execution_id: uuid.UUID, user_id: uuid.UUID) -> Execution:
    execution = db.scalar(select(Execution).where(Execution.id == execution_id, Execution.user_id == user_id))
    if execution is None:
        raise HTTPException(404)
    return execution


def owned_asset(db: Session, execution_id: uuid.UUID, asset_id: uuid.UUID, user_id: uuid.UUID) -> Asset:
    owned(db, execution_id, user_id)
    asset = db.scalar(select(Asset).where(Asset.id == asset_id, Asset.execution_id == execution_id,
                                         Asset.user_id == user_id, Asset.availability != "deleted"))
    if asset is None:
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
    app = FastAPI(title="FLAMORIS Studio")
    app.state.session_factory = session_factory or make_session_factory()
    app.state.gateway = gateway or GenerationGateway()
    app.state.thumbnails = thumbnails or Thumbnails(os.getenv("STUDIO_THUMBNAIL_DIR", ""))
    app.state.download_slots = asyncio.Semaphore(2)
    app.state.preview_admission = PreviewAdmission(app.state.download_slots)
    login_attempts = defaultdict(deque)

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
        return await call_next(request)

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
        attempts = login_attempts[address]
        while attempts and attempts[0] < time.monotonic() - 300:
            attempts.popleft()
        if len(attempts) >= 10:
            raise HTTPException(429)
        attempts.append(time.monotonic())
        if len(login_attempts) > 10_000:
            login_attempts.clear()  # bounded memory, deployment should also rate-limit at the edge
        user = db.scalar(select(User).where(User.email == normalized_email(input.email)))
        if user and user.locked_until and user.locked_until > now():
            raise HTTPException(401)
        try:
            valid = bool(user) and hasher.verify(user.password_hash, input.password)
        except VerificationError:
            valid = False
        if not valid:
            if user:
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
               _: uuid.UUID = Depends(current_user)):
        clear_session(response, db, request.cookies.get(COOKIE, ""))
        return {"ok": True}

    @app.post("/api/account/email")
    def change_email(input: EmailChange, db: Session = Depends(database),
                     user_id: uuid.UUID = Depends(current_user)):
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
                        user_id: uuid.UUID = Depends(current_user)):
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
    async def discovery(_: uuid.UUID = Depends(current_user)):
        return await app.state.gateway.discover()

    @app.get("/api/generation/image/preferences")
    def get_image_preferences(db: Session = Depends(database), user_id: uuid.UUID = Depends(current_user)):
        preference = db.get(ImagePreference, user_id)
        if preference is None:
            return {"width": 512, "height": 512, "steps": 20, "cfg": 7}
        return {key: getattr(preference, key) for key in ("width", "height", "steps", "cfg")}

    @app.put("/api/generation/image/preferences")
    def put_image_preferences(input: ImagePreferencesInput, db: Session = Depends(database),
                              user_id: uuid.UUID = Depends(current_user)):
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
    def list_styles(db: Session = Depends(database), user_id: uuid.UUID = Depends(current_user)):
        return {"items": [style_view(style) for style in db.scalars(select(ImageStyle)
                .where(ImageStyle.owner_user_id == user_id).order_by(ImageStyle.name, ImageStyle.id)).all()]}

    @app.post("/api/generation/image/styles", status_code=201)
    def create_style(input: StyleInput, db: Session = Depends(database),
                     u…14847 tokens truncated… assert listed["items"][0]["id"] == asset_id
    assert listed["items"][0]["hasThumbnail"] is False
    assert a.get(listed["items"][0]["thumbnailUrl"]).status_code == 200
    assert a.get(f"/api/assets/{asset_id}").json()["settings"]["positivePrompt"] == "a quiet stage"
    assert b.get("/api/assets").json()["items"] == []
    assert b.get(f"/api/assets/{asset_id}").status_code == 404
    assert a.post("/api/assets/delete", json={"ids": [asset_id]}).status_code == 403
    foreign = b.post("/api/assets/delete", json={"ids": [asset_id]}, headers={"X-CSRF-TOKEN": csrf_b})
    assert foreign.json()["results"] == [{"id": asset_id, "deleted": False, "error": "not_found"}]
    assert gateway.deleted == []

    gateway.fail_delete = True
    failed = a.post("/api/assets/delete", json={"ids": [asset_id]}, headers={"X-CSRF-TOKEN": csrf_a})
    assert failed.json()["results"][0]["deleted"] is False
    assert a.get("/api/assets").json()["items"][0]["id"] == asset_id
    gateway.fail_delete = False
    deleted = a.post("/api/assets/delete", json={"ids": [asset_id]}, headers={"X-CSRF-TOKEN": csrf_a})
    assert deleted.json()["results"] == [{"id": asset_id, "deleted": True}]
    assert gateway.deleted == ["private-asset"]
    assert a.get("/api/assets").json()["items"] == []
    assert a.get(f"/api/assets/{asset_id}").status_code == 404
    assert a.get(f"/api/executions/{execution_id}/assets/{asset_id}/download").status_code == 404
    assert a.get(f"/api/executions/{execution_id}/result").json()["assets"] == []
    assert a.post("/api/assets/delete", json={"ids": [asset_id]}, headers={"X-CSRF-TOKEN": csrf_a}).json()["results"][0]["deleted"]
    assert gateway.deleted == ["private-asset"]
    with factory() as db:
        record = db.get(Asset, uuid.UUID(asset_id))
        assert record.availability == "deleted" and record.thumbnail_locator is None


def test_bulk_delete_mixed_ownership_is_individual(clients):
    a, b, gateway, _ = clients
    csrf_a = register(a, "bulk-a@example.test")
    csrf_b = register(b, "bulk-b@example.test")
    made_a = a.post("/api/generation/image/jobs", json=image_request(), headers={"X-CSRF-TOKEN": csrf_a}).json()
    made_b = b.post("/api/generation/image/jobs", json=image_request(), headers={"X-CSRF-TOKEN": csrf_b}).json()
    asset_a = a.get(f"/api/executions/{made_a['id']}/result").json()["assets"][0]["id"]
    asset_b = b.get(f"/api/executions/{made_b['id']}/result").json()["assets"][0]["id"]
    response = a.post("/api/assets/delete", json={"ids": [asset_a, asset_b]},
                      headers={"X-CSRF-TOKEN": csrf_a}).json()["results"]
    assert response == [{"id": asset_a, "deleted": True},
                        {"id": asset_b, "deleted": False, "error": "not_found"}]
    assert b.get(f"/api/assets/{asset_b}").status_code == 200
    assert a.get("/api/assets").json()["items"] == []


def test_thumbnail_cleanup_error_is_retryable_after_upstream_delete(clients, monkeypatch):
    a, _, gateway, factory = clients
    csrf = register(a, "cleanup@example.test")
    execution_id = a.post("/api/generation/image/jobs", json=image_request(),
                          headers={"X-CSRF-TOKEN": csrf}).json()["id"]
    asset_id = a.get(f"/api/executions/{execution_id}/result").json()["assets"][0]["id"]
    assert a.get(f"/api/executions/{execution_id}/assets/{asset_id}/thumbnail").status_code == 200
    thumbnails = a.app.state.thumbnails
    original = thumbnails.delete
    attempts = []

    def fail_once(locator):
        attempts.append(locator)
        if len(attempts) == 1:
            raise OSError("thumbnail directory unavailable")
        return original(locator)

    monkeypatch.setattr(thumbnails, "delete", fail_once)
    response = a.post("/api/assets/delete", json={"ids": [asset_id]},
                      headers={"X-CSRF-TOKEN": csrf})
    assert response.json()["results"] == [{"id": asset_id, "deleted": True}]
    with factory() as db:
        assert db.get(Asset, uuid.UUID(asset_id)).thumbnail_locator is not None
    assert gateway.deleted == ["private-asset"]
    retry = a.post("/api/assets/delete", json={"ids": [asset_id]},
                   headers={"X-CSRF-TOKEN": csrf})
    assert retry.json()["results"] == [{"id": asset_id, "deleted": True}]
    assert gateway.deleted == ["private-asset"]
    assert len(attempts) == 2
    with factory() as db:
        record = db.get(Asset, uuid.UUID(asset_id))
        assert record.availability == "deleted" and record.thumbnail_locator is None


def test_large_asset_catalog_is_independent_of_binary_and_survives_restart(clients):
    a, b, gateway, factory = clients
    csrf = register(a, 'large@example.test')
    register(b, 'other@example.test')
    async def forbidden(*args, **kwargs):
        raise AssertionError('metadata sync attempted binary materialization')
    async def large_assets(job):
        return [{'asset_id': 'large-asset', 'filename': 'large.png', 'mime_type': 'image/png',
                 'media_kind': 'image', 'size_bytes': 80 * 1024 * 1024}]
    async def transfer_limit(asset_id):
        raise GatewayError("asset_too_large")
    gateway.result = forbidden
    gateway.content = forbidden
    gateway.assets = large_assets
    gateway.prepare_asset = transfer_limit
    created = a.post('/api/generation/image/jobs', json=image_request(), headers={'X-CSRF-TOKEN': csrf})
    execution_id = created.json()['id']
    result = a.get(f'/api/executions/{execution_id}/result')
    assert result.status_code == 200
    asset = result.json()['assets'][0]
    assert asset['sizeBytes'] == 80 * 1024 * 1024
    assert not asset['hasThumbnail']
    assert a.get('/api/assets').json()['items'][0]['id'] == asset['id']
    assert b.get(asset['downloadUrl']).status_code == 404
    rejected = a.get(asset['downloadUrl'])
    assert rejected.status_code == 413
    assert rejected.json()['error'] == 'asset_too_large'
    assert a.get(asset['thumbnailUrl']).status_code == 413
    gateway.status = forbidden
    again = create_app(factory, gateway, a.app.state.thumbnails)
    with TestClient(again) as restarted:
        restarted.cookies.update(a.cookies)
        assert restarted.get(f'/api/executions/{execution_id}/result').status_code == 200
        assert restarted.get('/api/assets').json()['items'][0]['id'] == asset['id']
    with factory() as db:
        assert len(list(db.scalars(select(Asset)))) == 1


def test_catalog_sync_retries_after_timeout_and_thumbnail_disk_failure(clients):
    a, _, gateway, _ = clients
    csrf = register(a, 'retry@example.test')
    created = a.post('/api/generation/image/jobs', json=image_request(), headers={'X-CSRF-TOKEN': csrf})
    endpoint = f"/api/executions/{created.json()['id']}/result"
    original = gateway.assets
    async def unavailable(*args):
        raise GatewayError('unavailable')
    gateway.assets = unavailable
    assert a.get(endpoint).status_code == 503
    gateway.assets = original
    result = a.get(endpoint)
    assert result.status_code == 200
    asset = result.json()['assets'][0]
    with patch.object(a.app.state.thumbnails, 'save', side_effect=OSError('disk full')):
        assert a.get(asset['thumbnailUrl']).status_code == 503
    gateway.assets = unavailable
    cached = a.get(endpoint)
    assert cached.status_code == 200
    assert cached.json()['catalogSync'] == 'unavailable'
    assert a.get('/api/assets').json()['items'][0]['id'] == asset['id']


@pytest.mark.asyncio
async def test_gateway_translates_upstream_size_limit_without_leaking_details():
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    gateway = GenerationGateway()
    class Session:
        async def call_tool(self, *args, **kwargs):
            return SimpleNamespace(is_error=True, content=[SimpleNamespace(
                text='ComfyUI output exceeds the 64 MiB download limit /private/path')])
    @asynccontextmanager
    async def connection():
        yield Session()
    gateway._connection = connection
    with pytest.raises(GatewayError) as error:
        await gateway.content('asset', 64 * 1024 * 1024)
    assert error.value.code == 'asset_too_large'
    assert '/private' not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["assets.prepare", "assets.read"])
async def test_gateway_identifies_only_exact_hub_missing_transfer_tool(name):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    gateway = GenerationGateway()

    class Session:
        async def call_tool(self, called, args, **kwargs):
            assert called == name
            return SimpleNamespace(is_error=True, content=[
                SimpleNamespace(text=f"Unknown tool: {called}")])

    @asynccontextmanager
    async def connection():
        yield Session()

    gateway._connection = connection
    with pytest.raises(GatewayError) as error:
        await gateway._call(name)
    assert error.value.code == "transfer_unavailable"

    class OtherSession:
        async def call_tool(self, called, args, **kwargs):
            return SimpleNamespace(is_error=True, content=[
                SimpleNamespace(text=f"Unknown tool: {called} /private/path")])

    @asynccontextmanager
    async def other_connection():
        yield OtherSession()

    gateway._connection = other_connection
    with pytest.raises(GatewayError) as other_error:
        await gateway._call(name)
    assert other_error.value.code == "upstream_failure"


@pytest.mark.parametrize("missing", ["assets.prepare", "assets.read"])
def test_missing_transfer_capability_has_safe_error_on_asset_routes(clients, missing):
    a, b, gateway, _ = clients
    csrf = register(a, "transfer-missing@example.test")
    register(b, "transfer-other@example.test")

    async def assets(job):
        return [{"asset_id": "private-asset", "filename": "photo.png",
                 "mime_type": "image/png", "media_kind": "image",
                 "size_bytes": None, "output_index": 0}]

    calls = []

    async def prepare(asset_id):
        calls.append("assets.prepare")
        if missing == "assets.prepare":
            raise GatewayError("transfer_unavailable")
        return {"asset_id": asset_id, "mime_type": "image/png", "size_bytes": len(gateway.image),
                "sha256": hashlib.sha256(gateway.image).hexdigest(),
                "chunk_bytes": 256 * 1024, "transfer_version": 1}

    async def read(asset_id, digest, offset, length):
        calls.append("assets.read")
        raise GatewayError("transfer_unavailable")

    gateway.assets = assets
    gateway.prepare_asset = prepare
    gateway.read_asset = read
    made = a.post("/api/generation/image/jobs", json=image_request(), headers={"X-CSRF-TOKEN": csrf})
    asset = a.get(f"/api/executions/{made.json()['id']}/result").json()["assets"][0]
    for route in ("previewUrl", "thumbnailUrl", "downloadUrl"):
        before = len(calls)
        assert b.get(asset[route]).status_code == 404
        assert len(calls) == before
        response = a.get(asset[route])
        assert response.status_code == 503
        assert response.json() == {"error": "transfer_unavailable",
                                   "message": "Asset transfer is unavailable. The service deployment is incomplete."}
        assert "private-asset" not in response.text
    assert calls.count(missing) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [10, 24])
async def test_gallery_burst_queues_bounded_thumbnails_and_cached_reads_bypass_slots(clients, count):
    import asyncio
    import httpx

    a, b, gateway, factory = clients
    csrf = register(a, 'preview-burst@example.test')
    register(b, 'preview-other@example.test')
    made = a.post('/api/generation/image/jobs', json=image_request(), headers={'X-CSRF-TOKEN': csrf})
    execution_id = uuid.UUID(made.json()['id'])
    ids = []
    with factory() as db:
        execution = db.get(Execution, execution_id)
        for i in range(count):
            asset = Asset(user_id=execution.user_id, execution_id=execution_id,
                          upstream_asset_id=f'burst-{i}', storage_locator=f'burst-{i}',
                          display_name=f'{i}.png', media_kind='image', mime_type='image/png', size_bytes=None)
            db.add(asset)
            db.flush()
            ids.append(asset.id)
        db.commit()
    digest = hashlib.sha256(gateway.image).hexdigest()
    prepared = []
    active = peak = 0

    async def prepare(asset_id):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        prepared.append(asset_id)
        await asyncio.sleep(0.02)
        active -= 1
        return {'asset_id': asset_id, 'mime_type': 'image/png', 'size_bytes': len(gateway.image),
                'sha256': digest, 'chunk_bytes': 256 * 1024, 'transfer_version': 1}

    async def read(asset_id, sha256, offset, length):
        data = gateway.image[offset:offset + length]
        return {'asset_id': asset_id, 'sha256': sha256, 'offset': offset,
                'size_bytes': len(gateway.image), 'data_base64': base64.b64encode(data).decode(),
                'chunk_sha256': hashlib.sha256(data).hexdigest(),
                'next_offset': offset + len(data), 'eof': offset + len(data) == len(gateway.image)}

    gateway.prepare_asset, gateway.read_asset = prepare, read
    urls = [f'/api/executions/{execution_id}/assets/{asset_id}/thumbnail' for asset_id in ids]
    assert b.get(urls[0]).status_code == 404
    assert not prepared
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=a.app),
                                 base_url='http://testserver', cookies=a.cookies) as client:
        responses = await asyncio.gather(*(client.get(url) for url in urls))
        assert [response.status_code for response in responses] == [200] * count
        assert all(response.headers['content-type'] == 'image/webp' for response in responses)
        assert len(prepared) == count and peak == 2
        slots = a.app.state.download_slots
        await slots.acquire()
        await slots.acquire()
        try:
            assert (await client.get(urls[0])).status_code == 200
            assert len(prepared) == count
        finally:
            slots.release()
            slots.release()
    assert a.app.state.preview_admission.pending == 0
