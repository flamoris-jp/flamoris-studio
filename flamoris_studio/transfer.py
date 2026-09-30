"""Verify bounded Generation MCP chunks without collecting the asset in memory."""
import asyncio
import base64
import binascii
import hashlib
import re
from contextlib import asynccontextmanager

from fastapi import HTTPException

from .gateway import GatewayError

CHUNK_BYTES = 256 * 1024
MAX_TRANSFER_BYTES = 1024**3


class PreviewAdmission:
    """Bound pending previews while sharing the existing transfer semaphore."""

    def __init__(self, slots, *, max_pending=24, timeout=30):
        self.slots = slots
        self.max_pending = max_pending
        self.timeout = timeout
        self.pending = 0

    @asynccontextmanager
    async def acquire(self):
        if self.pending >= self.max_pending:
            raise HTTPException(429, "Too many pending previews", headers={"Retry-After": "2"})
        self.pending += 1
        try:
            try:
                await asyncio.wait_for(self.slots.acquire(), timeout=self.timeout)
            except TimeoutError:
                raise HTTPException(429, "Preview transfer is busy", headers={"Retry-After": "2"}) from None
        finally:
            self.pending -= 1
        try:
            yield
        finally:
            self.slots.release()


def metadata(prepared: dict, asset_id: str, mime: str, max_bytes: int):
    size = prepared.get("size_bytes")
    digest = prepared.get("sha256")
    if (prepared.get("asset_id") != asset_id or prepared.get("mime_type") != mime or
        prepared.get("transfer_version") != 1 or type(size) is not int or
        not 0 < size <= min(max_bytes, MAX_TRANSFER_BYTES) or
        type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest) or
        type(prepared.get("chunk_bytes")) is not int or
        not 1 <= prepared["chunk_bytes"] <= CHUNK_BYTES):
        raise GatewayError("validation")
    return size, digest, prepared["chunk_bytes"]


def chunk(response: dict, asset_id: str, digest: str, offset: int, size: int, limit: int):
    encoded = response.get("data_base64")
    if (response.get("asset_id") != asset_id or response.get("sha256") != digest or
        type(response.get("offset")) is not int or response["offset"] != offset or
        type(response.get("size_bytes")) is not int or response["size_bytes"] != size or
        type(encoded) is not str or len(encoded) > 4 * ((limit + 2) // 3) + 4):
        raise GatewayError("validation")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise GatewayError("validation") from exc
    end = offset + len(data)
    if (not 0 < len(data) <= limit or end > size or
        response.get("chunk_sha256") != hashlib.sha256(data).hexdigest() or
        type(response.get("next_offset")) is not int or response["next_offset"] != end or
        response.get("eof") is not (end == size)):
        raise GatewayError("validation")
    return data


async def read_with_retry(attempt):
    """Retry a cursor-free read at its unchanged offset on transport failure only.

    The caller's attempt must recheck authorization and validate the chunk every time.
    """
    for retry in range(3):
        try:
            return await attempt()
        except GatewayError as exc:
            if exc.code != "unavailable" or retry == 2:
                raise
            await asyncio.sleep(0.1 * (retry + 1))
