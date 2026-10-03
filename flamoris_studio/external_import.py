"""Exact external provenance bindings and metadata-only per-user catalog import."""

import asyncio
import hashlib
import json
import os
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .auth import authenticated_user, current_user, database
from .db import Asset, Execution, ExternalAssetClaim, ExternalImport, User, now
from .gateway import GatewayError
from .media import generated_filename
from .result_contract import normalize_outputs

TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
CATALOG_LOCK = 0x535445585445524E


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    jobId: str = Field(pattern=r"^[a-f0-9]{32}$")


def provenance(value):
    if (type(value) is not dict or set(value) != {"issuer", "subject"} or any(
            type(value[key]) is not str or not TOKEN.fullmatch(value[key])
            for key in ("issuer", "subject"))):
        raise ValueError("Invalid external provenance")
    return dict(value)


def configured_binding(user_id):
    """Operator-provisioned grants; no browser field can assert a binding."""
    try:
        raw = os.getenv("STUDIO_EXTERNAL_BINDINGS", "[]")
        if len(raw.encode()) > 65536:
            raise ValueError()
        rows = json.loads(raw)
        if type(rows) is not list or len(rows) > 128:
            raise ValueError()
        users, subjects, selected = set(), set(), None
        for row in rows:
            if type(row) is not dict or set(row) != {"user_id", "issuer", "subject"}:
                raise ValueError()
            uid = str(uuid.UUID(row["user_id"]))
            actor = provenance({key: row[key] for key in ("issuer", "subject")})
            subject = (actor["issuer"], actor["subject"])
            if row["user_id"] != uid or uid in users or subject in subjects:
                raise ValueError()
            users.add(uid)
            subjects.add(subject)
            if uid == str(user_id):
                selected = actor
        endpoint = os.getenv("STUDIO_GENERATION_ENDPOINT", "")
        token = os.getenv("STUDIO_GENERATION_TOKEN", "")
        namespace = os.getenv("STUDIO_GENERATION_NAMESPACE", "")
        if selected is None or not endpoint or not token or namespace not in {"", "generation"}:
            raise ValueError()
        stamp = hashlib.sha256(json.dumps(
            [endpoint, token, namespace, selected], sort_keys=True).encode()).hexdigest()
        return selected, stamp
    except (ValueError, TypeError, AttributeError, KeyError):
        raise HTTPException(403, "External generation import is unavailable for this account") from None


def catalog_guard(db):
    # All ordinary catalog synchronization and external imports share this short
    # DB transaction lock, so a provenance claim cannot race an ordinary owner.
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": CATALOG_LOCK}):
        raise HTTPException(429, "Catalog update is busy", headers={"Retry-After": "2"})


def reauthorize(request, db, user, binding):
    db.rollback()
    if current_user(request, db) != user:
        raise HTTPException(401)
    if configured_binding(user) != binding:
        raise HTTPException(409, "External binding changed; retry after refreshing")


def checked_status(value, job_id, actor):
    try:
        if (type(value) is not dict or value.get("job_id") != job_id or
                value.get("status") != "completed" or
                provenance(value.get("external_provenance")) != actor):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        # Unknown jobs, anonymous jobs, unfinished jobs and someone else's
        # provenance share a response without exposing existence or metadata.
        raise HTTPException(404, "Completed external generation is unavailable") from None


def checked_outputs(listing, job_id, actor):
    try:
        if type(listing) is not list or not 1 <= len(listing) <= 64:
            raise ValueError()
        for item in listing:
            if (type(item) is not dict or item.get("job_id") != job_id or
                    type(item.get("asset_id")) is not str or not re.fullmatch(
                        re.escape(job_id) + r":[0-9]{3}", item["asset_id"]) or
                    provenance(item.get("external_provenance")) != actor):
                raise ValueError()
        return normalize_outputs(listing)
    except (ValueError, KeyError, TypeError, GatewayError):
        raise HTTPException(404, "Completed external generation is unavailable") from None


def mount_external_import(app, view):
    router = APIRouter(prefix="/api/generation/external-import")
    app.state.external_import_slots = asyncio.Semaphore(2)

    @router.get("")
    def availability(user: uuid.UUID = Depends(authenticated_user)):
        try:
            configured_binding(user)
            return {"available": True}
        except HTTPException:
            return {"available": False}

    @router.post("")
    async def import_completed(input: ImportRequest, request: Request,
                               db: Session = Depends(database),
                               user: uuid.UUID = Depends(authenticated_user)):
        binding = configured_binding(user)
        existing = db.get(ExternalImport, input.jobId)
        if existing is not None:
            if existing.user_id != user:
                raise HTTPException(404, "Completed external generation is unavailable")
            return view(db.get(Execution, existing.execution_id), db)
        db.rollback()
        # Read-only metadata operations are bounded and have no submission replay.
        if app.state.external_import_slots.locked():
            raise HTTPException(429, "External import is busy", headers={"Retry-After": "2"})
        async with app.state.external_import_slots:
            try:
                async with asyncio.timeout(100):
                    status = await app.state.gateway.status(input.jobId)
                    checked_status(status, input.jobId, binding[0])
                    listing = await app.state.gateway.assets(input.jobId)
                    outputs = checked_outputs(listing, input.jobId, binding[0])
                    # A changed terminal identity cannot be accepted after listing.
                    checked_status(await app.state.gateway.status(input.jobId), input.jobId, binding[0])
            except TimeoutError:
                raise GatewayError("unavailable") from None
        reauthorize(request, db, user, binding)
        db.rollback()
        catalog_guard(db)
        # Repeat owner/claim checks within the global catalog transaction. Claims
        # remain durable even when catalog entries are deleted from the gallery.
        if db.get(User, user) is None:
            raise HTTPException(401)
        existing = db.get(ExternalImport, input.jobId)
        if existing is not None:
            if existing.user_id != user:
                raise HTTPException(404, "Completed external generation is unavailable")
            return view(db.get(Execution, existing.execution_id), db)
        old_jobs = db.scalars(select(Execution).where(
            Execution.source == "generation", Execution.upstream_job_id == input.jobId)).all()
        if old_jobs:
            # An ordinary execution already established ownership. Never create
            # a second external ownership record for that existing job.
            if any(row.user_id != user for row in old_jobs):
                raise HTTPException(404, "Completed external generation is unavailable")
            return view(old_jobs[0], db)
        ids = [output.upstream_id for output in outputs]
        if db.scalar(select(Asset.id).where(Asset.upstream_asset_id.in_(ids))) is not None or any(
                db.get(ExternalAssetClaim, identifier) is not None for identifier in ids):
            raise HTTPException(404, "Completed external generation is unavailable")
        instant = now()
        kinds = {output.media_kind for output in outputs}
        category = next(iter(kinds)) if len(kinds) == 1 else "multimodal"
        execution = Execution(id=uuid.uuid4(), user_id=user, workflow="external-mcp",
            category=category, operation="external.import", upstream_job_id=input.jobId,
            request_snapshot={}, last_known_status="completed", submitted_at=instant,
            completed_at=instant)
        db.add(execution)
        db.flush()
        db.add(ExternalImport(upstream_job_id=input.jobId, user_id=user,
            execution_id=execution.id, binding_digest=binding[1]))
        for index, output in enumerate(outputs, 1):
            asset_id = uuid.uuid4()
            asset = Asset(id=asset_id, user_id=user, execution_id=execution.id,
                upstream_asset_id=output.upstream_id, storage_locator=output.upstream_id,
                original_filename=output.display_name,
                display_name=generated_filename(asset_id, instant, index, output.media_kind, output.mime_type),
                media_kind=output.media_kind, mime_type=output.mime_type, size_bytes=output.size_bytes,
                extra_metadata={"external_import": True,
                    **({"output_role": output.role} if output.role else {})})
            db.add(asset)
            db.flush()
            db.add(ExternalAssetClaim(upstream_asset_id=output.upstream_id, user_id=user, asset_id=asset_id))
        db.commit()
        return view(execution, db)

    app.include_router(router)
