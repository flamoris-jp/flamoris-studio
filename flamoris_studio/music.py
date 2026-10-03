"""Owned native Music generation/transcription over the shared job and Asset routes."""
import hashlib
import json
import os
import re
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import authenticated_user, current_user, database
from .db import Execution, GenerationRequestRecord
from .generation_requests import existing_request
from .gateway import GatewayError
from .managed_inputs import check_input, owned_input, quota_guard, usable
from .music_contract import MusicRequest, TranscriptionRequest, contract


class MusicSubmission(MusicRequest):
    requestId: str = Field(pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")


class TranscriptionSubmission(TranscriptionRequest):
    requestId: str = Field(pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")


def configured_route():
    return tuple(os.getenv(key, "") for key in ("STUDIO_GENERATION_ENDPOINT", "STUDIO_GENERATION_TOKEN", "STUDIO_GENERATION_NAMESPACE", "STUDIO_MUSIC_ENABLED"))


def mount_music(app, view, set_status):
    router = APIRouter(prefix="/api/generation/music")

    @router.get("/discovery")
    async def discovery(user: uuid.UUID = Depends(authenticated_user)):
        if os.getenv("STUDIO_MUSIC_ENABLED", "false").lower() != "true":
            return {"generate": False, "transcribe": False}
        return await app.state.gateway.discover_music()

    async def submit(input, operation, request, db, user):
        request_id, profile = uuid.UUID(input.requestId), contract(operation)
        original = input.model_dump(exclude={"requestId"})
        request_digest = hashlib.sha256(json.dumps([original, profile], sort_keys=True, allow_nan=False).encode()).hexdigest()
        cached = existing_request(db, user, request_id, request_digest, "music." + operation)
        if cached is not None:
            return view(cached, db)
        reference = None
        if operation == "transcribe":
            reference = owned_input(db, uuid.UUID(input.referenceInputId), user)
            if reference.mime_type != "audio/wav" or not usable(reference):
                raise HTTPException(409, "Audio reference unavailable; choose another Asset")
        db.rollback()
        route = configured_route()
        if os.getenv("STUDIO_MUSIC_ENABLED", "false").lower() != "true":
            raise GatewayError("unavailable")
        availability = await app.state.gateway.discover_music()
        if type(availability) is not dict or availability.get(operation) is not True:
            raise GatewayError("unavailable")
        upstream_input = await check_input(app.state.gateway, reference) if reference else None
        parameters = input.model_dump(exclude={"requestId", "referenceInputId"})
        if operation == "generate":
            for name in ("seed", "lm_seed"):
                if parameters[name] is None:
                    parameters[name] = secrets.randbelow(2**53)
        else:
            parameters["audio"] = upstream_input
        db.rollback()
        if current_user(request, db) != user:
            raise HTTPException(401)
        if reference:
            quota_guard(db)
            db.refresh(reference)
            if reference.owner_user_id != user or not usable(reference) or reference.mime_type != "audio/wav":
                raise HTTPException(409, "Audio reference unavailable")
        snapshot = {**{key: value for key, value in parameters.items() if key != "audio"},
                    "musicContract" if operation == "generate" else "transcriptionContract": profile["music" if operation == "generate" else "transcription"], "snapshotVersion": 1}
        if reference:
            snapshot["referenceInputId"] = str(reference.id)
        execution = Execution(user_id=user, workflow=profile["id"], category="music", operation="music." + operation,
                              reference_input_id=reference.id if reference else None, request_snapshot=snapshot)
        db.add(execution)
        try:
            db.flush()
            db.add(GenerationRequestRecord(user_id=user, request_id=request_id, execution_id=execution.id, request_digest=request_digest))
            db.commit()
        except IntegrityError:
            db.rollback()
            cached = existing_request(db, user, request_id, request_digest, "music." + operation)
            if cached is None:
                raise HTTPException(503, "Music request admission could not be confirmed") from None
            return view(cached, db)
        try:
            workflow = await app.state.gateway.build_music(parameters, operation)
            db.rollback()
            if current_user(request, db) != user:
                raise HTTPException(401)
            if configured_route() != route:
                raise HTTPException(409, "Music configuration changed; refresh before submitting")
            if reference:
                db.refresh(reference)
                if not usable(reference):
                    raise HTTPException(409, "Audio reference expired before submission")
        except (GatewayError, HTTPException):
            set_status(db, execution, "failed")
            raise
        try:
            job = await app.state.gateway.submit(workflow)
            if type(job) is not dict or type(job.get("job_id")) is not str or not re.fullmatch(r"[a-f0-9]{32}", job["job_id"]) or job.get("status") not in {"queued", "running", "completed", "failed", "cancelled", "unknown"}:
                raise GatewayError("upstream_failure")
            execution.upstream_job_id = job["job_id"]
            set_status(db, execution, job["status"])
        except GatewayError as exc:
            set_status(db, execution, "busy" if exc.code == "busy" else "submission_unknown")
        db.rollback()
        if current_user(request, db) != user:
            raise HTTPException(401)
        return view(execution, db)

    @router.post("/generate/jobs", status_code=201)
    async def generate(input: MusicSubmission, request: Request, db: Session = Depends(database), user: uuid.UUID = Depends(authenticated_user)):
        return await submit(input, "generate", request, db, user)

    @router.post("/transcribe/jobs", status_code=201)
    async def transcribe(input: TranscriptionSubmission, request: Request, db: Session = Depends(database), user: uuid.UUID = Depends(authenticated_user)):
        return await submit(input, "transcribe", request, db, user)

    @router.get("/requests/{request_id}")
    def request_status(request_id: uuid.UUID, db: Session = Depends(database), user: uuid.UUID = Depends(authenticated_user)):
        cached = existing_request(db, user, request_id)
        if cached is None or cached.category != "music":
            raise HTTPException(404)
        return view(cached, db)

    app.include_router(router)
