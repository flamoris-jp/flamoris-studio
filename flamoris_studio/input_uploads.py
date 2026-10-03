"""Authenticated raw image uploads; the upstream owns the original bytes."""

import asyncio
import hashlib
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from .auth import authenticated_user, current_user, database
from .db import ManagedInput, now
from .gateway import GatewayError
from .managed_inputs import input_view, owned_input, reserve, validate_metadata

MAX_UPLOAD_BYTES = 8 * 1024**2
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}


async def publish_upload(app, db, row, data):
    try:
        # The random private identity and full storage charge were committed
        # before the first RPC. Unknown begin/finish cannot orphan authority.
        raw = await app.state.gateway.upload_input(row.upstream_input_id, data, row.mime_type)
        validate_metadata(raw, row, creating=True)
        row.expires_at = datetime.fromtimestamp(raw["expires_at"], timezone.utc)
        locator = None
        try:
            locator = app.state.input_thumbnails.save(row.id, data)
        except (ValueError, OSError):
            pass
        if locator:
            row.accounted_bytes = row.size_bytes + len(app.state.input_thumbnails.load(locator) or b"")
        else:
            # A failed save may have published. Release only after confirmed cleanup.
            app.state.input_thumbnails.delete(row.thumbnail_locator)
            row.thumbnail_locator = None
            row.accounted_bytes = row.size_bytes
        row.state = "live"
        db.commit()
        return input_view(row)
    except BaseException:
        key = row.id
        db.rollback()
        persisted = db.get(ManagedInput, key)
        if persisted:
            compensated = False
            try:
                # Cleanup has its own bound even when the outer upload deadline
                # already cancelled this task. An unavailable backend must not
                # hold the upload admission lock indefinitely.
                async with asyncio.timeout(5):
                    result = await app.state.gateway.delete_input(persisted.upstream_input_id)
                compensated = isinstance(result, dict) and result.get("deleted") is True
            except (GatewayError, asyncio.CancelledError, TimeoutError):
                pass
            persisted.state = "revoked" if compensated else "create_unknown"
            persisted.terminal_at = now() if compensated else persisted.expires_at
            try:
                db.commit()
            except SQLAlchemyError:
                db.rollback()
        raise


def mount_input_uploads(app):
    # One body/transfer per process, with immediate rejection rather than a
    # growing queue of requests buffering private images in memory.
    app.state.input_upload_lock = asyncio.Lock()

    @app.post("/api/generation/inputs/upload", status_code=201)
    async def upload_image(request: Request, db=Depends(database), user_id=Depends(authenticated_user)):
        mime = request.headers.get("content-type", "").lower()
        if mime not in IMAGE_TYPES:
            raise HTTPException(415, "Choose a PNG, JPEG or WebP image")
        length = request.headers.get("content-length")
        if length is not None and (len(length) > 10 or not length.isascii()
                                   or not length.isdigit() or not 0 < int(length) <= MAX_UPLOAD_BYTES):
            raise HTTPException(413, "Reference images must be at most 8 MiB")
        if app.state.input_upload_lock.locked():
            raise HTTPException(429, "Another reference image is uploading; try again later")
        try:
            async with app.state.input_upload_lock, asyncio.timeout(90):
                content = bytearray()
                async for part in request.stream():
                    if len(content) + len(part) > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "Reference images must be at most 8 MiB")
                    content.extend(part)
                if not content or length is not None and len(content) != int(length):
                    raise HTTPException(422, "Reference image upload was incomplete")
                db.rollback()
                if current_user(request, db) != user_id:
                    raise HTTPException(401)
                row = reserve(db, user_id, None, app.state.input_limits,
                              mime_type=mime, upload_bytes=len(content))
                row.checksum = hashlib.sha256(content).hexdigest()
                db.commit()
                result = await publish_upload(app, db, row, bytes(content))
                db.rollback()
                if current_user(request, db) != user_id:
                    raise HTTPException(401)
                owned_input(db, row.id, user_id)
                return result
        except TimeoutError:
            raise HTTPException(504, "Reference upload could not be confirmed; no automatic retry was made") from None
