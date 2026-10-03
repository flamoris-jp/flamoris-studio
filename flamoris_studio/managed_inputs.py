"""Studio ownership, transactional quota reservations and bounded retention."""

import asyncio
import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from .db import Execution, ManagedInput, now
from .gateway import GatewayError

THUMB_BYTES = 256 * 1024
TERMINAL_STATES = {"completed", "failed", "cancelled", "busy"}
LOCK_ID = 761042917


@dataclass(frozen=True)
class Limits:
    user_rows: int = 128
    global_rows: int = 1024
    user_bytes: int = 32 * 1024**2
    global_bytes: int = 128 * 1024**2

    @classmethod
    def from_env(cls):
        values = {
            key: int(os.getenv("STUDIO_INPUT_" + key.upper(), str(default)))
            for key, default in cls().__dict__.items()
        }
        if any(
            not 1 <= value <= (4096 if key.endswith("rows") else 1024**3)
            for key, value in values.items()
        ):
            raise ValueError("Invalid managed input quota configuration")
        return cls(**values)


def quota_guard(db):
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK_ID}):
        raise HTTPException(409, "Reference storage is busy")


def owned_input(db, input_id, user_id):
    row = db.scalar(
        select(ManagedInput).where(
            ManagedInput.id == input_id, ManagedInput.owner_user_id == user_id
        )
    )
    if row is None:
        raise HTTPException(404)
    return row


def usable(row):
    return row.state == "live" and row.expires_at > now()


def input_view(row):
    return {
        "id": str(row.id),
        "sourceAssetId": str(row.source_asset_id) if row.source_asset_id else None,
        "mimeType": row.mime_type,
        "sizeBytes": row.size_bytes,
        "expiresAt": row.expires_at.isoformat(),
        "available": usable(row),
        "thumbnailUrl": f"/api/generation/inputs/{row.id}/thumbnail"
        if row.thumbnail_locator
        else None,
    }


def reserve(db, user_id, source, limits, *, mime_type=None, upload_bytes=0):
    quota_guard(db)
    charged = THUMB_BYTES + upload_bytes
    total, byte_total = db.execute(
        select(
            func.count(ManagedInput.id),
            func.coalesce(func.sum(ManagedInput.accounted_bytes), 0),
        )
    ).one()
    owned, byte_owned = db.execute(
        select(
            func.count(ManagedInput.id),
            func.coalesce(func.sum(ManagedInput.accounted_bytes), 0),
        ).where(ManagedInput.owner_user_id == user_id)
    ).one()
    if (
        total >= limits.global_rows
        or owned >= limits.user_rows
        or byte_total + charged > limits.global_bytes
        or byte_owned + charged > limits.user_bytes
    ):
        raise HTTPException(409, "Reference storage quota reached")
    row = ManagedInput(
        id=uuid.uuid4(),
        owner_user_id=user_id,
        source_asset_id=source.id if source else None,
        source_upstream_id=source.upstream_asset_id if source else None,
        mime_type=source.mime_type if source else mime_type,
        upstream_input_id=uuid.uuid4().hex if source is None else None,
        size_bytes=upload_bytes if source is None else None,
        state="reserved",
        accounted_bytes=charged,
        expires_at=now() + timedelta(hours=24),
    )
    # Pre-record the only possible thumbnail locator before touching the filesystem.
    row.thumbnail_locator = row.id.hex
    db.add(row)
    db.commit()
    return row


def validate_metadata(raw, row, *, creating=False):
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("input_id"), str)
        or not re.fullmatch(r"[a-f0-9]{32}", raw["input_id"])
        or raw.get("source_asset_id") != row.source_upstream_id
        or row.mime_type is not None and raw.get("mime_type") != row.mime_type
        or (raw.get("mime_type"), raw.get("media_kind")) not in {
            ("image/png", "image"), ("image/jpeg", "image"), ("image/webp", "image"),
            ("audio/wav", "audio")}
        or type(raw.get("size_bytes")) is not int
        or not 0 < raw["size_bytes"] <= 64 * 1024**2
        or not isinstance(raw.get("sha256"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", raw["sha256"])
        or type(raw.get("expires_at")) not in (int, float)
        or not now().timestamp() < raw["expires_at"] <= now().timestamp() + 86400
    ):
        raise GatewayError("validation")
    if not creating and (
        raw["input_id"] != row.upstream_input_id
        or raw["sha256"] != row.checksum
        or raw["mime_type"] != row.mime_type
        or raw["size_bytes"] != row.size_bytes
    ):
        raise GatewayError("validation")
    if row.source_upstream_id is None and (
        raw.get("source_kind") != "upload"
        or raw["input_id"] != row.upstream_input_id
        or raw["size_bytes"] != row.size_bytes
        or raw["sha256"] != row.checksum
        or raw["media_kind"] != "image"
    ):
        raise GatewayError("validation")
    return raw


async def check_input(gateway, row):
    if not usable(row):
        raise HTTPException(409, "Reference unavailable; choose another Asset")
    raw = await gateway.get_input(row.upstream_input_id)
    validate_metadata(raw, row)
    return row.upstream_input_id


def protected(db, input_id):
    return (
        db.scalar(
            select(Execution.id)
            .where(
                Execution.reference_input_id == input_id,
                Execution.last_known_status.not_in(TERMINAL_STATES),
            )
            .limit(1)
        )
        is not None
    )


def prune(db, thumbnails):
    """An indexed <=100-row batch; file failures retain durable charged rows."""
    quota_guard(db)
    instant = now()
    # Expiry promotion is bounded, and reserved/unknown creates use their TTL too.
    expired = db.scalars(
        select(ManagedInput)
        .where(ManagedInput.terminal_at.is_(None), ManagedInput.expires_at <= instant)
        .order_by(ManagedInput.expires_at, ManagedInput.id)
        .limit(100)
    ).all()
    for row in expired:
        row.terminal_at = row.expires_at
    db.commit()
    quota_guard(db)
    rows = db.scalars(
        select(ManagedInput)
        .where(
            ManagedInput.terminal_at <= instant - timedelta(hours=24),
            ~select(Execution.id)
            .where(
                Execution.reference_input_id == ManagedInput.id,
                Execution.last_known_status.not_in(TERMINAL_STATES),
            )
            .exists(),
        )
        .order_by(ManagedInput.terminal_at, ManagedInput.id)
        .limit(100)
    ).all()
    count = 0
    for row in rows:
        if protected(db, row.id):
            continue
        # The marker is committed before deletion so failure/restart stays accounted.
        row.state = "pending_delete"
        db.commit()
        quota_guard(db)
        row = db.get(ManagedInput, row.id)
        if row is None or protected(db, row.id):
            continue
        try:
            thumbnails.delete(row.thumbnail_locator)
        except OSError:
            continue
        db.delete(row)
        db.commit()
        count += 1
        quota_guard(db)
    db.commit()
    return count


async def reconcile_expired(app, cursor=None):
    """Observe <=100 known jobs in <=60 seconds, without holding DB locks over IO."""
    query = (
        select(Execution.id, Execution.user_id, Execution.reference_input_id, Execution.upstream_job_id)
        .join(ManagedInput, Execution.reference_input_id == ManagedInput.id)
        .where(
            ManagedInput.owner_user_id == Execution.user_id,
            ManagedInput.expires_at <= now(),
            Execution.upstream_job_id.is_not(None),
            Execution.last_known_status.not_in(TERMINAL_STATES),
        )
        .order_by(Execution.id)
        .limit(100)
    )
    with app.state.session_factory() as db:
        rows = db.execute(query.where(Execution.id > cursor) if cursor else query).all()
        if not rows and cursor:
            rows = db.execute(query).all()
    deadline = asyncio.get_running_loop().time() + 60
    for execution_id, owner_id, reference_id, job_id in rows:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        cursor = execution_id  # Failed/unknown jobs cannot starve later rows.
        try:
            raw = await asyncio.wait_for(app.state.gateway.status(job_id), min(5, remaining))
        except (GatewayError, TimeoutError):
            continue
        if (
            not isinstance(raw, dict)
            or raw.get("job_id") != job_id
            or not isinstance(raw.get("status"), str)
            or raw.get("status") not in {"completed", "failed", "cancelled"}
        ):
            continue
        with app.state.session_factory() as db:
            # Recheck the mapping after IO; never overwrite a concurrent terminal result.
            db.execute(
                update(Execution)
                .where(
                    Execution.id == execution_id,
                    Execution.user_id == owner_id,
                    Execution.reference_input_id == reference_id,
                    Execution.upstream_job_id == job_id,
                    Execution.last_known_status.not_in(TERMINAL_STATES),
                )
                .values(
                    last_known_status=raw["status"],
                    updated_at=now(),
                    completed_at=func.coalesce(Execution.completed_at, now()),
                )
            )
            db.commit()
    return cursor


async def maintenance(app):
    cursor = None
    while True:
        try:
            cursor = await reconcile_expired(app, cursor)
            with app.state.session_factory() as db:
                prune(db, app.state.input_thumbnails)
        except (SQLAlchemyError, HTTPException, OSError):
            pass
        await asyncio.sleep(3600)


async def create_snapshot(app, db, row, content_reader):
    raw = None
    try:
        raw = await app.state.gateway.create_input(row.source_upstream_id)
        validate_metadata(raw, row, creating=True)
        row.upstream_input_id = raw["input_id"]
        row.checksum = raw["sha256"]
        row.mime_type = raw["mime_type"]
        row.size_bytes = raw["size_bytes"]
        row.expires_at = datetime.fromtimestamp(raw["expires_at"], timezone.utc)
        db.commit()  # Preserve private identity for bounded compensation/recovery.
        locator = None
        try:
            if row.mime_type == "audio/wav":
                # WAV snapshots stay upstream. No full audio copy or image thumbnail.
                row.state = "live"
                row.thumbnail_locator = None
                row.accounted_bytes = 0
                db.commit()
                return input_view(row)
            data, mime = await content_reader()
            if (
                mime != row.mime_type
                or len(data) != row.size_bytes
                or hashlib.sha256(data).hexdigest() != row.checksum
            ):
                raise GatewayError("validation")
            locator = app.state.input_thumbnails.save(row.id, data)
        except (ValueError, OSError, GatewayError, HTTPException):
            # Preview failure cannot invent access or invalidate the immutable input.
            locator = None
        row.state = "live"
        if locator:
            row.accounted_bytes = len(app.state.input_thumbnails.load(locator) or b"")
        else:
            # A save may have published before failing. Retain the reservation and
            # pre-recorded locator until a confirmed deletion, never forget its bytes.
            app.state.input_thumbnails.delete(row.thumbnail_locator)
            row.thumbnail_locator = None
            row.accounted_bytes = 0
        db.commit()
        return input_view(row)
    except BaseException:
        db.rollback()
        compensated = False
        if (
            isinstance(raw, dict)
            and isinstance(raw.get("input_id"), str)
            and re.fullmatch(r"[a-f0-9]{32}", raw["input_id"])
        ):
            try:
                result = await app.state.gateway.delete_input(raw["input_id"])
                compensated = result.get("deleted") is True
            except (GatewayError, asyncio.CancelledError):
                pass
        persisted = db.get(ManagedInput, row.id)
        if persisted:
            persisted.state = "revoked" if compensated else "create_unknown"
            persisted.terminal_at = now() if compensated else persisted.expires_at
            try:
                db.commit()
            except SQLAlchemyError:
                db.rollback()
        raise
