import base64
import hashlib

import pytest

from flamoris_studio.gateway import GatewayError
from flamoris_studio.transfer import chunk, metadata, read_with_retry


def test_transfer_contract_rejects_unbounded_or_corrupt_chunks():
    payload = b"image data"
    digest = hashlib.sha256(payload).hexdigest()
    info = {"asset_id": "a", "mime_type": "image/png", "size_bytes": len(payload),
            "sha256": digest, "chunk_bytes": 256, "transfer_version": 1}
    assert metadata(info, "a", "image/png", 1024) == (len(payload), digest, 256)
    with pytest.raises(GatewayError):
        metadata({**info, "size_bytes": 1025}, "a", "image/png", 1024)
    result = {"asset_id": "a", "sha256": digest, "offset": 0,
              "size_bytes": len(payload), "data_base64": base64.b64encode(payload).decode(),
              "chunk_sha256": digest, "next_offset": len(payload), "eof": True}
    assert chunk(result, "a", digest, 0, len(payload), 256) == payload
    with pytest.raises(GatewayError):
        chunk({**result, "chunk_sha256": "0" * 64}, "a", digest, 0, len(payload), 256)
    with pytest.raises(GatewayError):
        chunk({**result, "next_offset": len(payload) + 1}, "a", digest, 0, len(payload), 256)


@pytest.mark.asyncio
async def test_read_retries_same_offset_after_transient_unavailability(monkeypatch):
    attempts = []
    delays = []

    async def no_wait(seconds):
        delays.append(seconds)

    monkeypatch.setattr("flamoris_studio.transfer.asyncio.sleep", no_wait)

    async def attempt():
        # An endpoint callback includes authentication and ownership checks.
        attempts.append(("owner_checked", 256))
        if len(attempts) < 3:
            raise GatewayError("unavailable")
        return b"verified chunk"

    assert await read_with_retry(attempt) == b"verified chunk"
    assert attempts == [("owner_checked", 256)] * 3
    assert delays == [0.1, 0.2]


@pytest.mark.asyncio
@pytest.mark.parametrize("code, expected_attempts", [
    ("unavailable", 3), ("validation", 1),
    ("upstream_failure", 1), ("asset_too_large", 1),
])
async def test_read_retry_is_bounded_and_error_specific(monkeypatch, code, expected_attempts):
    async def no_wait(_):
        pass

    monkeypatch.setattr("flamoris_studio.transfer.asyncio.sleep", no_wait)
    attempts = 0

    async def attempt():
        nonlocal attempts
        attempts += 1
        raise GatewayError(code)

    with pytest.raises(GatewayError) as error:
        await read_with_retry(attempt)
    assert error.value.code == code
    assert attempts == expected_attempts


@pytest.mark.asyncio
@pytest.mark.parametrize('count', [10, 24])
async def test_preview_burst_waits_without_exceeding_shared_transfer_limit(count):
    import asyncio
    from flamoris_studio.transfer import PreviewAdmission

    slots = asyncio.Semaphore(2)
    admission = PreviewAdmission(slots)
    active = peak = 0

    async def preview():
        nonlocal active, peak
        async with admission.acquire():
            active += 1
            peak = max(peak, active)
            # Longer than the old 10ms admission timeout.
            await asyncio.sleep(0.02)
            active -= 1
            return b'preview'

    assert await asyncio.gather(*(preview() for _ in range(count))) == [b'preview'] * count
    assert peak == 2 and admission.pending == 0
    assert not slots.locked()


@pytest.mark.asyncio
async def test_preview_admission_bounds_waiters_and_recovers_after_timeout_and_cancel():
    import asyncio
    from fastapi import HTTPException
    from flamoris_studio.transfer import PreviewAdmission

    slots = asyncio.Semaphore(1)
    await slots.acquire()  # An existing download owns the shared slot.
    admission = PreviewAdmission(slots, max_pending=1, timeout=0.03)

    async def preview():
        async with admission.acquire():
            return 'ok'

    waiting = asyncio.create_task(preview())
    await asyncio.sleep(0)
    with pytest.raises(HTTPException) as overflow:
        await preview()
    assert overflow.value.status_code == 429
    assert overflow.value.headers == {'Retry-After': '2'}
    with pytest.raises(HTTPException) as timeout:
        await waiting
    assert timeout.value.status_code == 429 and admission.pending == 0
    waiting = asyncio.create_task(preview())
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert admission.pending == 0 and slots.locked()
    slots.release()
    assert await preview() == 'ok'
    with pytest.raises(ValueError):
        async with admission.acquire():
            raise ValueError('upstream failure')
    assert await preview() == 'ok'


@pytest.mark.asyncio
async def test_cancelled_active_preview_releases_shared_slot():
    import asyncio
    from flamoris_studio.transfer import PreviewAdmission

    slots = asyncio.Semaphore(1)
    admission = PreviewAdmission(slots)
    started = asyncio.Event()

    async def preview():
        async with admission.acquire():
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(preview())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with admission.acquire():
        assert slots.locked()
    assert admission.pending == 0 and not slots.locked()
