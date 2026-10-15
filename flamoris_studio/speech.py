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
from .generation_requests import existing_request
from .speech_contract import (
    HTTP_PROOF, PROOF, REFERENCE_PROOF, REFERENCE_TEMPLATE, SpeechRequest, TEMPLATE, exact,
)
from .managed_inputs import check_input, owned_input, quota_guard, usable


class SpeechSubmission(SpeechRequest):
    referenceInputId: str | None = Field(default=None,
        pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")
    requestId: str = Field(pattern=r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")



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
        if input.referenceInputId is None:
            original.pop("referenceInputId")
        identity_proof = REFERENCE_PROOF if input.referenceInputId else PROOF
        request_digest = hashlib.sha256(json.dumps(
            [original, identity_proof], sort_keys=True, allow_nan=False).encode()).hexdigest()
        cached = existing_request(db, user, request_id, request_digest, "speech.generate")
        if cached is not None:
            return view(cached, db)
        reference = None
        if input.referenceInputId:
            reference = owned_input(db, uuid.UUID(input.referenceInputId), user)
            if reference.mime_type != "audio/wav" or not usable(reference):
                raise HTTPException(409, "Reference audio unavailable")
        db.rollback()
        route = configured_route()
        if os.getenv("STUDIO_SPEECH_ENABLED", "false").lower() != "true":
            raise GatewayError("unavailable")
        availability = await app.state.gateway.discover_speech()
        if type(availability) is not dict or availability.get("available") is not True:
            raise GatewayError("unavailable")
        if reference:
            if (availability.get("referenceAvailable") is not True
                    or not exact(availability.get("referenceContract"), REFERENCE_PROOF)):
                raise GatewayError("unavailable")
            upstream_reference = await check_input(app.state.gateway, reference)
        proof = availability.get("speechContract", PROOF)
        if not any(exact(proof, allowed) for allowed in (PROOF, HTTP_PROOF)):
            raise GatewayError("validation")
        if input.seed is None:
            input.seed = secrets.randbelow(2**53)
        parameters = input.model_dump(exclude={"requestId", "referenceInputId"})
        if reference:
            parameters["reference_audio"] = upstream_reference
            proof = REFERENCE_PROOF
        db.rollback()
        if current_user(request, db) != user:
            raise HTTPException(401)
        if reference:
            quota_guard(db)
            db.refresh(reference)
            if (reference.owner_user_id != user or not usable(reference)
                    or reference.mime_type != "audio/wav"):
                raise HTTPException(409, "Reference audio unavailable")
        snapshot = {key: value for key, value in parameters.items() if key != "reference_audio"}
        snapshot.update(speechContract=proof, snapshotVersion=1)
        if reference:
            snapshot.update(referenceInputId=str(reference.id), referenceSha256=reference.checksum)
        execution = Execution(
            user_id=user, workflow=REFERENCE_TEMPLATE if reference else TEMPLATE,
            category="speech", operation="speech.generate",
            reference_input_id=reference.id if reference else None, request_snapshot=snapshot,
        )
        db.add(execution)
        try:
            db.flush()
            db.add(SpeechRequestRecord(user_id=user, request_id=request_id, execution_id=execution.id,
                                       request_digest=request_digest))
            db.commit()
        except IntegrityError:
            db.rollback()
            cached = existing_request(db, user, request_id, request_digest, "speech.generate")
            if cached is None:
                raise HTTPException(503, "Speech request admission could not be confirmed") from None
            return view(cached, db)
        try:
            workflow = (await app.state.gateway.build_reference_speech(parameters) if reference
                        else await app.state.gateway.build_speech(parameters, proof)
                        if exact(proof, HTTP_PROOF)
                        else await app.state.gateway.build_speech(parameters))
            db.rollback()
            if current_user(request, db) != user:
                raise HTTPException(401)
            if reference:
                db.refresh(reference)
                if not usable(reference):
                    raise HTTPException(409, "Reference audio expired before submission")
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
        cached = existing_request(db, user, request_id, operation="speech.generate")
        if cached is None:
            raise HTTPException(404)
        return view(cached, db)

    app.include_router(router)

