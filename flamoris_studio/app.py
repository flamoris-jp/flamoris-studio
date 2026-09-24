"""Studio HTTP boundary: all user-owned lookups include the authenticated owner."""
import os
import uuid
import time
from collections import defaultdict, deque
from datetime import timedelta

from argon2.exceptions import VerificationError
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import COOKIE, CSRF_COOKIE, clear_session, current_user, database, digest, hasher, new_csrf, require_csrf, start_session
from .db import Asset, Execution, LoginSession, User, make_session_factory, now
from .gateway import GatewayError, GenerationGateway
from .media import Thumbnails, filename, inspect_image
from .logging_setup import configure_logging


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=256)
    password: str = Field(min_length=12, max_length=256)


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
    seed: int = Field(ge=0, le=2**64 - 1)
    checkpoint: str = Field(min_length=1, max_length=1024)
    loras: list[Lora] = Field(default_factory=list, max_length=16)

    def parameters(self):
        return {"positive_prompt": self.positivePrompt, "negative_prompt": self.negativePrompt,
                "width": self.width, "height": self.height, "steps": self.steps,
                "cfg": self.cfg, "seed": self.seed, "checkpoint": self.checkpoint,
                "loras": [{"name": item.name, "strength_model": item.strengthModel,
                           "strength_clip": item.strengthClip} for item in self.loras]}


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
    login_attempts = defaultdict(deque)

    @app.exception_handler(GatewayError)
    async def gateway_error(_, exc: GatewayError):
        code = exc.code if exc.code in {"busy", "unavailable", "validation", "upstream_failure"} else "upstream_failure"
        return JSONResponse(status_code={"busy": 409, "unavailable": 503, "validation": 422,
                                         "upstream_failure": 502}[code],
                            content={"error": code, "message": {"busy": "Generation service is busy.",
                                "unavailable": "Generation service is unavailable.",
                                "validation": "Invalid generation result.",
                                "upstream_failure": "Generation service failed."}[code]})

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
        email = input.email.strip().lower()
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
        user = db.scalar(select(User).where(User.email == input.email.strip().lower()))
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

    @app.get("/api/system/status")
    def status():
        return {"healthy": True, "service": "studio"}

    @app.get("/api/generation/image/discovery")
    async def discovery(_: uuid.UUID = Depends(current_user)):
        return await app.state.gateway.discover()

    @app.post("/api/generation/image/jobs", status_code=201)
    async def submit(input: ImageRequest, db: Session = Depends(database), user_id: uuid.UUID = Depends(current_user)):
        capability = await app.state.gateway.discover()
        template = "text-to-image-lora" if input.loras else "text-to-image"
        if not capability["available"] or template not in capability["templates"]:
            raise GatewayError("unavailable")
        workflow = await app.state.gateway.build(template, input.parameters())
        execution = Execution(user_id=user_id, workflow=template, request_snapshot=input.model_dump())
        db.add(execution)
        db.commit()  # Persist uncertain submissions before calling the non-idempotent upstream tool.
        try:
            job = await app.state.gateway.submit(workflow)
            execution.upstream_job_id = job["job_id"]
            set_status(db, execution, job["status"])
        except GatewayError as exc:
            set_status(db, execution, "busy" if exc.code == "busy" else "submission_unknown")
            raise
        return view(execution, db)

    @app.get("/api/assets")
    def list_generated_assets(limit: int = Query(default=24, ge=1, le=48),
                              offset: int = Query(default=0, ge=0, le=100000),
                              db: Session = Depends(database),
                              user_id: uuid.UUID = Depends(current_user)):
        rows = db.execute(select(Asset, Execution).join(Execution, Asset.execution_id == Execution.id)
                          .where(Asset.user_id == user_id, Execution.user_id == user_id,
                                 Asset.availability != "deleted")
                          .order_by(Asset.created_at.desc(), Asset.id.desc())
                          .offset(offset).limit(limit + 1)).all()
        return {"items": [asset_view(asset) for asset, _ in rows[:limit]],
                "nextOffset": offset + limit if len(rows) > limit else None}

    @app.get("/api/assets/{asset_id}")
    def generated_asset_detail(asset_id: uuid.UUID, db: Session = Depends(database),
                               user_id: uuid.UUID = Depends(current_user)):
        row = db.execute(select(Asset, Execution).join(Execution, Asset.execution_id == Execution.id)
                         .where(Asset.id == asset_id, Asset.user_id == user_id,
                                Execution.user_id == user_id, Asset.availability != "deleted")).first()
        if row is None:
            raise HTTPException(404)
        asset, execution = row
        request = execution.request_snapshot if isinstance(execution.request_snapshot, dict) else {}
        settings = {key: request[key] for key in ("positivePrompt", "negativePrompt", "checkpoint",
                    "seed", "steps", "cfg", "width", "height") if key in request}
        return {**asset_view(asset), "state": execution.last_known_status,
                "submittedAt": execution.submitted_at.isoformat(), "settings": settings}

    @app.post("/api/assets/delete")
    async def delete_generated_assets(input: DeleteAssetsRequest, db: Session = Depends(database),
                                      user_id: uuid.UUID = Depends(current_user)):
        results = []
        for asset_id in dict.fromkeys(input.ids):
            # Serialize with result catalog updates, including a stale assets.list response.
            row = db.execute(select(Asset, Execution).join(Execution, Asset.execution_id == Execution.id)
                             .where(Asset.id == asset_id, Asset.user_id == user_id,
                                    Execution.user_id == user_id)
                             .with_for_update(of=Execution)).first()
            if row is None:
                results.append({"id": str(asset_id), "deleted": False, "error": "not_found"})
                db.rollback()
                continue
            asset, _ = row
            if asset.availability == "deleted":
                results.append({"id": str(asset_id), "deleted": True})
                db.rollback()
                continue
            try:
                upstream = await app.state.gateway.delete_asset(asset.upstream_asset_id)
                if upstream.get("deleted") is not True:
                    raise GatewayError("upstream_failure")
                asset.availability = "deleted"
                asset.updated_at = now()
                locator = asset.thumbnail_locator
                asset.thumbnail_locator = None
                db.commit()
                app.state.thumbnails.delete(locator)
                results.append({"id": str(asset_id), "deleted": True})
            except GatewayError as exc:
                db.rollback()
                results.append({"id": str(asset_id), "deleted": False, "error": exc.code})
        return {"results": results}

    @app.get("/api/executions/{execution_id}")
    async def execution_status(execution_id: uuid.UUID, db: Session = Depends(database),
                               user_id: uuid.UUID = Depends(current_user)):
        execution = owned(db, execution_id, user_id)
        if execution.upstream_job_id:
            job = await app.state.gateway.status(execution.upstream_job_id)
            set_status(db, execution, job["status"])
        return view(execution, db)

    @app.get("/api/executions/{execution_id}/result")
    async def result(execution_id: uuid.UUID, db: Session = Depends(database),
                     user_id: uuid.UUID = Depends(current_user)):
        execution = owned(db, execution_id, user_id)
        if not execution.upstream_job_id:
            return view(execution, db)
        job = await app.state.gateway.status(execution.upstream_job_id)
        set_status(db, execution, job["status"])
        if job["status"] != "completed":
            return view(execution, db)
        final = await app.state.gateway.result(execution.upstream_job_id)
        set_status(db, execution, final["status"])
        if final["status"] != "completed":
            return view(execution, db)
        listing = await app.state.gateway.assets(execution.upstream_job_id)
        # jobs.result files and assets.list output_index both follow the
        # snapshot's output order. Never derive a path from the asset ID or
        # use a stored path to serve content; assets.get remains authoritative.
        files = final.get("files", [])
        output_paths = {}
        if isinstance(files, list):
            for index, file in enumerate(files[:64]):
                if isinstance(file, dict) and isinstance(file.get("file"), str):
                    path = file["file"]
                    if 0 < len(path) <= 4096 and os.path.isabs(path):
                        output_paths[index] = path
        # Lock the parent row to serialize concurrent catalog updates across processes.
        db.execute(text("SELECT id FROM executions WHERE id = :id FOR UPDATE"), {"id": execution.id})
        known = {asset.upstream_asset_id: asset for asset in db.scalars(
            select(Asset).where(Asset.execution_id == execution.id, Asset.user_id == user_id))}
        for item in listing[:64]:
            if item.get("media_kind") != "image" or item.get("mime_type") not in {"image/png", "image/jpeg", "image/webp"}:
                continue
            upstream = item.get("asset_id")
            if not isinstance(upstream, str) or not 0 < len(upstream) <= 256:
                continue
            output_index = item.get("output_index")
            storage_path = output_paths.get(output_index) if type(output_index) is int else None
            if upstream in known:
                if storage_path is not None:
                    known[upstream].storage_path = storage_path
                continue
            original = item.get("filename") if isinstance(item.get("filename"), str) else None
            asset = Asset(user_id=user_id, execution_id=execution.id, upstream_asset_id=upstream,
                         storage_locator=upstream, storage_path=storage_path, original_filename=original,
                         display_name=filename(original), media_kind="image", mime_type=item["mime_type"],
                         size_bytes=item.get("size_bytes") if isinstance(item.get("size_bytes"), int) else None)
            db.add(asset)
            known[upstream] = asset
        db.commit()
        for asset in db.scalars(select(Asset).where(Asset.execution_id == execution.id,
                                                   Asset.thumbnail_locator.is_(None)).limit(8)):
            try:
                data, mime = await get_content(asset, db)
                asset.thumbnail_locator = app.state.thumbnails.save(asset.id, data)
                db.commit()
            except (GatewayError, ValueError):
                pass
        return view(execution, db)

    @app.post("/api/executions/{execution_id}/cancel")
    async def cancel(execution_id: uuid.UUID, db: Session = Depends(database),
                     user_id: uuid.UUID = Depends(current_user)):
        execution = owned(db, execution_id, user_id)
        if execution.upstream_job_id and execution.last_known_status not in {"completed", "failed", "cancelled"}:
            job = await app.state.gateway.cancel(execution.upstream_job_id)
            set_status(db, execution, job["status"])
        return view(execution, db)

    async def get_content(asset: Asset, db: Session):
        max_bytes = min(max(int(os.getenv("STUDIO_MAX_ASSET_BYTES", "67108864")), 1), 67108864)
        if asset.size_bytes and asset.size_bytes > max_bytes:
            raise GatewayError("validation")
        data, mime = await app.state.gateway.content(asset.upstream_asset_id, max_bytes)
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
    async def thumbnail(execution_id: uuid.UUID, asset_id: uuid.UUID, db: Session = Depends(database),
                        user_id: uuid.UUID = Depends(current_user)):
        asset = owned_asset(db, execution_id, asset_id, user_id)
        if asset.thumbnail_locator is None:
            data, _ = await get_content(asset, db)
            asset.thumbnail_locator = app.state.thumbnails.save(asset.id, data)
            db.commit()
        data = app.state.thumbnails.load(asset.thumbnail_locator) if asset.thumbnail_locator else None
        if data is None:
            raise HTTPException(404)
        return Response(data, media_type="image/webp", headers={"Cache-Control": "private, no-store",
                         "X-Content-Type-Options": "nosniff"})

    @app.get("/api/executions/{execution_id}/assets/{asset_id}/content")
    @app.get("/api/executions/{execution_id}/assets/{asset_id}/download")
    async def asset_content(execution_id: uuid.UUID, asset_id: uuid.UUID, request: Request,
                            db: Session = Depends(database), user_id: uuid.UUID = Depends(current_user)):
        asset = owned_asset(db, execution_id, asset_id, user_id)
        data, mime = await get_content(asset, db)
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
