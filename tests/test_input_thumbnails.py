import io
import os
import uuid

import pytest
from PIL import Image

from flamoris_studio.input_thumbnails import InputThumbnails


def test_independent_bounded_thumbnail_roundtrip_and_recorded_temp_cleanup(tmp_path):
    store = InputThumbnails(tmp_path / "inputs")
    source = io.BytesIO()
    Image.new("RGB", (1024, 768)).save(source, format="PNG")
    locator = store.save(uuid.uuid4(), source.getvalue())
    raw = store.load(locator)
    assert len(raw) <= 256 * 1024
    with Image.open(io.BytesIO(raw)) as image:
        assert image.size == (512, 384)
    (store.root / (locator + ".webp.tmp")).write_bytes(b"interrupted")
    store.delete(locator)
    assert store.load(locator) is None
    assert list(store.root.iterdir()) == []


def test_nonregular_and_symlink_thumbnail_reads_fail_closed_without_blocking(tmp_path):
    store = InputThumbnails(tmp_path)
    locator = uuid.uuid4().hex
    path = tmp_path / (locator + ".webp")
    os.mkfifo(path)
    assert store.load(locator) is None
    path.unlink()
    outside = tmp_path / "outside"
    outside.write_bytes(b"private")
    path.symlink_to(outside)
    with pytest.raises(OSError):
        store.load(locator)
    with pytest.raises(ValueError):
        store.load("../../outside")
