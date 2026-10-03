"""Owner-scoped native Speech submission using the shared Generation job authority."""

import os
import re
import secrets
import uuid
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from pydantic import Field

from .auth import authenticated_user, current_user, database
from .db import Execution, SpeechRequestRecord
from .gateway import GatewayError
from .speech_contract import PROOF, SpeechRequest, TEMPLATE


class SpeechSubmission(SpeechRequest):
    requestId: str = Field(pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")


def existing_request(db, user, request_id, request_digest=None):
    record = db.get(SpeechRequestRecord, (user, request_id))
    if record is not None and request_digest is not None and record.request_digest != request_digest:
        raise HTTPException(409, "This request identifier already belongs to another speech request")
    return db.get(Execution, record.execution_id) if record is not None else None


def configured_route():
    return tuple(os.getenv(key, "") for key in (
        "STUDIO_GENERATION_ENDPOINT", "STUDIO_GENERATION_TOKEN", "STUDIO_GENERATION_NAMESPACE",
        "STUDIO_SPEECH_ENABLED"))


def mount_speech(app, view, set_status):
    router = APIRouter(prefix="/api/generation/speech")

    @router.get("/discovery")
    async def discovery(user: uuid.UUID = Depends(authenticated_user)):
        if os.getenv("STUDIO_SPEECH_ENABLED", "false").lower() != "true":
            return {"available": False}
        return await app.state.gateway.discover_speech()

    @router.post("/jobs", status_code=201)
    async def submit(input: SpeechSubmission, request: Request,
                     db: Session = Depends(database), user: uuid.UUID = Depends(authenticated_user)):
        request_id = uuid.UUID(input.requestId)
        original = input.model_dump(exclude={"requestId"})
        request_digest = hashlib.sha256(json.dumps(
            [original, PROOF], sort_keys=True, allow_nan=False).encode()).hexdigest()
        cached = existing_request(db, user, request_id, request_digest)
        if cached is not None:
            return view(cached, db)
        db.rollback()
        route = configured_route()
        if os.getenv("STUDIO_SPEECH_ENABLED", "false").lower() != "true":
            raise GatewayError("unavailable")
        availability = await app.state.gateway.discover_speech()
        if type(availability) is not dict or availability.get("available") is not True:
            raise GatewayError("unavailable")
        if input.seed is None:
            input.seed = secrets.randbelow(2**53)
        parameters = input.model_dump(exclude={"requestId"})
        db.rollback()
        if current_user(request, db) != user:
            raise HTTPException(401)
        execution = Execution(user_id=user, workflow=TEMPLATE, category="speech", operation="speech.generate",
            request_snapshot={**parameters, "speechContract": PROOF, "snapshotVersion": 1})
        db.add(execution)
        try:
            db.flush()
            db.add(SpeechRequestRecord(user_id=user, request_id=request_id, execution_id=execution.id,
                                       request_digest=request_digest))
            db.commit()
        except IntegrityError:
            db.rollback()
            cached = existing_request(db, user, request_id, request_digest)
            if cached is None:
                raise HTTPException(503, "Speech request admission could not be confirmed") from None
            return view(cached, db)
        try:
            workflow = await app.state.gateway.build_speech(parameters)
            db.rollback()
            if current_user(request, db) != user:
                raise HTTPException(401)
            if configured_route() != route:
                raise HTTPException(409, "Speech configuration changed; refresh before submitting")
        except (GatewayError, HTTPException):
            set_status(db, execution, "failed")
            raise
        # Persisted execution/seed precede the one non-idempotent jobs.submit.
        try:
            job = await app.state.gateway.submit(workflow)
            if (type(job) is not dict or type(job.get("job_id")) is not str or
                    not re.fullmatch(r"[a-f0-9]{32}", job["job_id"]) or
                    job.get("status") not in {"queued", "running", "completed", "failed", "cancelled", "unknown"}):
                raise GatewayError("upstream_failure")
            execution.upstream_job_id = job["job_id"]
            set_status(db, execution, job["status"])
        except GatewayError as exc:
            set_status(db, execution, "busy" if exc.code == "busy" else "submission_unknown")
            # The Studio execution was created before submission. Return its
            # owned handle and uncertainty fence without replaying generation.
        db.rollback()
        if current_user(request, db) != user:
            raise HTTPException(401)
        return view(execution, db)

    @router.get("/requests/{request_id}")
    def request_status(request_id: uuid.UUID, db: Session = Depends(database),
                       user: uuid.UUID = Depends(authenticated_user)):
        cached = existing_request(db, user, request_id)
        if cached is None:
            raise HTTPException(404)
        return view(cached, db)

    app.include_router(router)
