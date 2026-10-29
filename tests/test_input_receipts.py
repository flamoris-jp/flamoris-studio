from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from flamoris_studio import managed_inputs
from flamoris_studio.input_uploads import publish_upload
from flamoris_studio.managed_inputs import ReceiptValidationError, validate_metadata

OBSERVED = datetime(2026, 10, 29, 6, 0, tzinfo=timezone.utc)


def receipt(offset=0, lifetime=86400, *, snapshot=False):
    created = OBSERVED.timestamp() + offset
    raw = {"input_id": "a" * 32, "source_asset_id": "asset-source" if snapshot else None,
           "mime_type": "image/png", "media_kind": "image", "size_bytes": 156,
           "sha256": "b" * 64, "created_at": created, "expires_at": created + lifetime}
    if not snapshot:
        raw["source_kind"] = "upload"
    row = SimpleNamespace(id=uuid4(), source_asset_id=None,
                          source_upstream_id=raw["source_asset_id"], upstream_input_id=raw["input_id"],
                          mime_type=raw["mime_type"], size_bytes=raw["size_bytes"], checksum=raw["sha256"],
                          thumbnail_locator="c" * 32, accounted_bytes=300000)
    return raw, row


@pytest.mark.parametrize("offset", [-0.1, 0, 0.1, 1])
@pytest.mark.parametrize("creating,snapshot", [(True, False), (False, False), (True, True), (False, True)])
def test_bounded_clock_offset_keeps_declared_and_local_lifetime_bounds(monkeypatch, offset, creating, snapshot):
    monkeypatch.setattr(managed_inputs, "now", lambda: OBSERVED)
    raw, row = receipt(offset, snapshot=snapshot)
    before = dict(raw)
    accepted = validate_metadata(raw, row, creating=creating)
    assert accepted["expires_at"] == min(raw["expires_at"], OBSERVED.timestamp() + 86400)
    assert accepted["expires_at"] <= raw["expires_at"]
    assert raw == before  # Never rewrite the remote receipt.


@pytest.mark.parametrize("change,reason", [
    ({"input_id": "invalid"}, "identity"),
    ({"input_id": "d" * 32}, "identity"),
    ({"source_asset_id": "different"}, "provenance"),
    ({"source_kind": "asset"}, "provenance"),
    ({"mime_type": "image/jpeg"}, "media"),
    ({"mime_type": []}, "media"),
    ({"media_kind": "audio"}, "media"),
    ({"size_bytes": 155}, "size"),
    ({"sha256": "e" * 64}, "digest"),
    ({"created_at": None}, "timestamps"),
    ({"created_at": True}, "timestamps"),
    ({"created_at": float("nan")}, "timestamps"),
    ({"expires_at": float("inf")}, "timestamps"),
    ({"expires_at": 10**1000}, "timestamps"),
    ({"expires_at": OBSERVED.timestamp()}, "timestamps"),
    ({"expires_at": OBSERVED.timestamp() + 86400.01}, "lifetime"),
    ({"created_at": OBSERVED.timestamp() + 1.001, "expires_at": OBSERVED.timestamp() + 86401.001}, "clock"),
    ({"created_at": OBSERVED.timestamp() - 10, "expires_at": OBSERVED.timestamp() - 1}, "expired"),
])
def test_invalid_receipts_keep_exact_field_and_expiry_fences(monkeypatch, change, reason):
    monkeypatch.setattr(managed_inputs, "now", lambda: OBSERVED)
    raw, row = receipt()
    with pytest.raises(ReceiptValidationError) as failure:
        validate_metadata({**raw, **change}, row, creating=True)
    assert failure.value.reason == "receipt_" + reason
    assert failure.value.code == "validation"
    assert str(failure.value) == "Generation service error"
    assert "asset-source" not in str(failure.value)


def test_clock_tolerance_does_not_extend_a_short_receipt_or_accept_an_expired_one(monkeypatch):
    monkeypatch.setattr(managed_inputs, "now", lambda: OBSERVED)
    raw, row = receipt(offset=0.5, lifetime=2)
    assert validate_metadata(raw, row, creating=True)["expires_at"] == OBSERVED.timestamp() + 2
    monkeypatch.setattr(managed_inputs, "now", lambda: OBSERVED + timedelta(seconds=3))
    with pytest.raises(ReceiptValidationError) as failure:
        validate_metadata(raw, row, creating=True)
    assert failure.value.reason == "receipt_expired"


@pytest.mark.asyncio
async def test_published_upload_stores_capped_local_expiry(monkeypatch):
    monkeypatch.setattr(managed_inputs, "now", lambda: OBSERVED)
    raw, row = receipt(offset=0.1)
    async def upload(*args):
        return raw
    thumbnails = Mock(); thumbnails.save.return_value = None
    app = SimpleNamespace(state=SimpleNamespace(gateway=SimpleNamespace(upload_input=upload), input_thumbnails=thumbnails))
    db = Mock()
    result = await publish_upload(app, db, row, b"fixture")
    assert row.expires_at == OBSERVED + timedelta(hours=24)
    assert row.state == "live" and result["available"]
    assert raw["expires_at"] > row.expires_at.timestamp()


def test_receipt_diagnostics_never_accept_an_arbitrary_reason():
    assert ReceiptValidationError("private raw data").reason == "receipt_metadata"
