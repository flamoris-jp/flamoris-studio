import base64
import hashlib

import pytest

from flamoris_studio.gateway import GatewayError
from flamoris_studio.transfer import chunk, metadata


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
