import io
import os
import re
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError

MIMES = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
Image.MAX_IMAGE_PIXELS = 32_000_000


def filename(raw: str | None) -> str:
    leaf = (raw or "image").replace("\\", "/").split("/")[-1]
    return re.sub(r"[^\w.\-]", "", leaf, flags=re.ASCII)[:100].strip(".") or "image"


def inspect_image(data: bytes, mime: str) -> tuple[int, int]:
    if mime not in MIMES or not data or len(data) > 64 * 1024 * 1024:
        raise ValueError("Invalid image")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != MIMES[mime] or min(image.size) < 1 or max(image.size) > 8192:
                raise ValueError("Invalid image")
            if image.width * image.height > 32_000_000:
                raise ValueError("Image is too large")
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            return image.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid image") from exc


class Thumbnails:
    def __init__(self, root: str):
        self.root = Path(root).resolve() if root else None

    def save(self, asset_id: uuid.UUID, data: bytes) -> str | None:
        if self.root is None:
            return None
        with Image.open(io.BytesIO(data)) as image:
            image.thumbnail((256, 256))
            output = io.BytesIO()
            image.save(output, "WEBP", quality=70)
        thumb = output.getvalue()
        if not 0 < len(thumb) <= 256 * 1024:
            return None
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if sum(p.stat().st_size for p in self.root.glob("*.webp") if p.is_file()) + len(thumb) > 1024**3:
            return None
        target = self.root / f"{asset_id.hex}.webp"
        temp = self.root / f"{uuid.uuid4().hex}.tmp"
        try:
            with open(temp, "xb") as file:
                file.write(thumb)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        return asset_id.hex

    def load(self, locator: str) -> bytes | None:
        if self.root is None or not re.fullmatch(r"[a-f0-9]{32}", locator):
            return None
        path = self.root / f"{locator}.webp"
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 256 * 1024:
            return None
        return path.read_bytes()
