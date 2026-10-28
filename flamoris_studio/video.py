"""Owner-scoped Video orchestration; Controller owns execution and media."""

import hashlib
import json
import os
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import authenticated_user, current_user, database
from .db import Execution, ExecutionInput, GenerationRequestRecord
from .gateway import GatewayError
from .generation_requests import existing_request
from .managed_inputs import check_input, owned_input, quota_guard, usable
from .video_contract import video_parameters


class VideoSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestId: uuid.UUID
    workflowId: str = Field(pattern=r"^comfy-[a-f0-9]{64}$")
    definitionVersion: int = Field(ge=1, le=1, strict=True)
    definitionDigest: str = Field(pattern=r"^[a-f0-9]{64}$")
    additionalParameters: dict = Field(default_factory=dict, max_length=64)
    imageInputs: dict[str, uuid.UUID] = Field(min_length=1, max_length=2)


def configured_route():
    return tuple(os.getenv(key, "") for key in (
        "STUDIO_GENERATION_ENDPOINT", "STUDIO_GENERATION_TOKEN", "STUDIO_GENERATION_NAMESPACE"))


def access(request, db, user, route):
    db.rollback()
    if current_user(request, db) != user:
        raise HTTPException(401)
    if configured_route() != route:
        raise HTTPException(409, "Video connection changed; refresh before submitting")


def available_image(row, user):
    if (row.owner_user_id != user or not usable(row)
        or row.mime_type not in {"image/png", "image/jpeg", "image/webp"}
        or not row.size_bytes or row.size_bytes > 8 * 1024**2):
        raise HTTPException(409, "Image unavailable; upload or choose another image")


def mount_video(app, view, set_status):
    router = APIRouter(prefix="/api/generation/video")

    @router.get("/discovery")
    async def discovery(request: Request, db: Session = Depends(database), user: uuid.UUID = Depends(authenticated_user)):
        route = configured_route()
        value = await app.state.gateway.discover_video()
        access(request, db, user, route)
        return value

    @router.post("/jobs", status_code=201)
    async def submit(input: VideoSubmission, request: Request, db: Session = Depends(database), user: uuid.UUID = Depends(authenticated_user)):
        original = input.model_dump(mode="json", exclude={"requestId"})
        try:
            request_digest = hashlib.sha256(json.dumps(original, sort_keys=True, allow_nan=False).encode()).hexdigest()
        except (ValueError, TypeError):
            raise HTTPException(422, "Invalid video parameters") from None
        cached = existing_request(db, user, input.requestId, request_digest, "video.generate")
        if cached is not None:
            return view(cached, db)
        # Reject another account's input before any shared upstream call.
        references = {role: owned_input(db, handle, user) for role, handle in input.imageInputs.items()}
        for row in references.values():
            available_image(row, user)
        db.rollback()
        route = configured_route()
        discovery = await app.state.gateway.discover_video()
        access(request, db, user, route)
        selected = next((item for item in discovery.get("workflows", []) if item["id"] == input.workflowId), None)
        if (discovery.get("available") is not True or discovery.get("managedInputReady") is not True
            or not selected or not selected["selectable"] or selected.get("recipeSchema") != 11
            or selected.get("definitionVersion") != input.definitionVersion
            or selected.get("definitionDigest") != input.definitionDigest):
            raise HTTPException(409, "Video recipe unavailable; refresh and select it again")
        try:
            parameters = video_parameters(selected, original)
        except (ValueError, TypeError, KeyError):
            raise HTTPException(422, "Unsupported video parameters or image roles") from None
        quota_guard(db)
        for row in sorted(references.values(), key=lambda item: item.id.int):
            db.refresh(row, with_for_update=True)
            available_image(row, user)
        snapshot = {**original, "additionalParameters": {key: value for key, value in parameters.items() if key not in references},
                    "videoMode": selected["video"]["mode"], "snapshotVersion": 4}
        execution = Execution(user_id=user, workflow=selected["id"], category="video", operation="video.generate", request_snapshot=snapshot)
        db.add(execution)
        try:
            db.flush()
            for role, row in references.items():
                db.add(ExecutionInput(execution_id=execution.id, role=role, input_id=row.id))
            db.add(GenerationRequestRecord(user_id=user, request_id=input.requestId, execution_id=execution.id, request_digest=request_digest))
            db.commit()
        except IntegrityError:
            db.rollback()
            cached = existing_request(db, user, input.requestId, request_digest, "video.generate")
            if cached is None:
                raise HTTPException(503, "Video request admission could not be confirmed") from None
            return view(cached, db)
        try:
            upstream = dict(parameters)
            for role, row in references.items():
                upstream[role] = await check_input(app.state.gateway, row)
            workflow = await app.state.gateway.build_selected(selected, upstream)
            access(request, db, user, route)
            for row in references.values():
                db.refresh(row)
                available_image(row, user)
        except (GatewayError, HTTPException):
            set_status(db, execution, "failed")
            raise
        # The request and every input fence were persisted before this one POST.
        try:
            job = await app.state.gateway.submit(workflow)
            if (type(job) is not dict or type(job.get("job_id")) is not str
                or not re.fullmatch(r"[a-f0-9]{32}", job["job_id"])
                or type(job.get("status")) is not str
                or job.get("operation") != "video.generate" or job.get("workflow_id") != workflow
                or job.get("status") not in {"queued", "running", "completed", "failed", "cancelled", "unknown"}):
                raise GatewayError("upstream_failure")
            execution.upstream_job_id = job["job_id"]
            set_status(db, execution, job["status"])
        except GatewayError as error:
            set_status(db, execution, "busy" if error.code == "busy" else "submission_unknown")
        access(request, db, user, route)
        return view(execution, db)

    @router.get("/requests/{request_id}")
    def request_status(request_id: uuid.UUID, db: Session = Depends(database), user: uuid.UUID = Depends(authenticated_user)):
        execution = existing_request(db, user, request_id, operation="video.generate")
        if execution is None:
            raise HTTPException(404)
        return view(execution, db)

    app.include_router(router)
