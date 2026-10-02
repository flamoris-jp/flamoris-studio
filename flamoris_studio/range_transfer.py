"""Single HTTP ranges over authenticated immutable Generation representation receipts."""

import asyncio
import hashlib
import re
import time

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from .gateway import GatewayError
from .media import filename
from .transfer import chunk, metadata, read_with_retry

TRANSFER_SECONDS = 300


def single_range(header, size):
    match = (
        re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", header) if len(header) <= 128 else None
    )
    if not match or not any(match.groups()):
        raise HTTPException(
            416, "Unsupported byte range", headers={"Content-Range": f"bytes */{size}"}
        )
    first, last = match.groups()
    if first:
        start, end = int(first), min(int(last), size - 1) if last else size - 1
    else:
        count = int(last)
        start, end = max(0, size - count), size - 1
        if not count:
            start = size
    if start >= size or end < start:
        raise HTTPException(
            416,
            "Unsatisfiable byte range",
            headers={"Content-Range": f"bytes */{size}"},
        )
    return start, end


class OwnedStreamingResponse(StreamingResponse):
    def __init__(self, *args, release, **kwargs):
        super().__init__(*args, **kwargs)
        self.release = release

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Covers header-send failure/disconnect before the generator starts.
            self.release()


async def representation_response(
    *,
    gateway,
    prepare,
    authorize,
    slots,
    upstream_id,
    mime,
    display_name,
    max_bytes,
    range_headers=(),
    if_range=None,
    allow_range=False,
    attachment=False,
):
    deadline = time.monotonic() + TRANSFER_SECONDS
    try:
        await asyncio.wait_for(slots.acquire(), timeout=0.01)
    except TimeoutError:
        raise HTTPException(429, "Too many active transfers") from None
    released = False

    def release():
        nonlocal released
        if not released:
            released = True
            slots.release()

    async def bounded(attempt):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GatewayError("unavailable")
        try:
            async with asyncio.timeout(remaining):
                return await attempt()
        except TimeoutError:
            raise GatewayError("unavailable") from None

    try:
        prepared = await bounded(prepare)
        if (
            type(prepared.get("size_bytes")) is int
            and prepared["size_bytes"] > max_bytes
        ):
            raise GatewayError("asset_too_large")
        size, digest, limit = metadata(prepared, upstream_id, mime, max_bytes)
        etag = f'"sha256-{digest}"'
        start, end, status = 0, size - 1, 200
        if allow_range and range_headers and (if_range is None or if_range == etag):
            if len(range_headers) != 1:
                raise HTTPException(
                    416,
                    "Unsupported byte range",
                    headers={"Content-Range": f"bytes */{size}"},
                )
            selected = single_range(range_headers[0], size)
            start, end = selected
            status = 206

        async def read_at(offset):
            requested = min(limit, end - offset + 1)

            async def attempt():
                await authorize()
                response = await gateway.read_asset(
                    upstream_id, digest, offset, requested
                )
                data = chunk(response, upstream_id, digest, offset, size, requested)
                await authorize()
                return data

            return await bounded(lambda: read_with_retry(attempt))

        first = await read_at(start)
    except BaseException:
        release()
        raise

    async def stream():
        offset, data = start, first
        hasher = hashlib.sha256() if start == 0 and end == size - 1 else None
        try:
            while True:
                await bounded(authorize)
                if hasher is not None:
                    hasher.update(data)
                offset += len(data)
                if (
                    offset == end + 1
                    and hasher is not None
                    and hasher.hexdigest() != digest
                ):
                    raise GatewayError("validation")
                yield data
                if offset == end + 1:
                    break
                data = await read_at(offset)
        finally:
            release()

    headers = {
        "Content-Length": str(end - start + 1),
        "Cache-Control": "private, no-store",
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }
    if allow_range:
        headers["Accept-Ranges"] = "bytes"
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if attachment:
        headers["Content-Disposition"] = (
            f'attachment; filename="{filename(display_name)}"'
        )
    return OwnedStreamingResponse(
        stream(), release=release, status_code=status, media_type=mime, headers=headers
    )
