"""Confined durable thumbnails whose locators are reserved in PostgreSQL first."""

import io
import os
import re
import stat
from pathlib import Path

from PIL import Image


class InputThumbnails:
    def __init__(self, root):
        self.root = Path(root) if root else None

    def directory(self):
        if self.root is None:
            return None
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        return os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    @staticmethod
    def name(locator):
        if not isinstance(locator, str) or not re.fullmatch(r"[a-f0-9]{32}", locator):
            raise ValueError("Invalid input thumbnail locator")
        return locator + ".webp"

    def save(self, input_id, data):
        with Image.open(io.BytesIO(data)) as image:
            if (
                getattr(image, "n_frames", 1) != 1
                or max(image.size) > 8192
                or image.width * image.height > 16 * 1024**2
            ):
                raise ValueError("Invalid reference image")
            image.thumbnail((512, 512))
            output = io.BytesIO()
            image.save(output, "WEBP", quality=70)
        raw = output.getvalue()
        if not 0 < len(raw) <= 256 * 1024:
            return None
        fd = self.directory()
        if fd is None:
            return None
        # The temporary name is derived from the persisted input locator too;
        # crash cleanup knows both names without scanning an unbounded directory.
        name = self.name(input_id.hex)
        temp = name + ".tmp"
        try:
            file = os.open(
                temp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=fd,
            )
            with os.fdopen(file, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
            return input_id.hex
        finally:
            os.close(fd)

    def load(self, locator):
        name = self.name(locator)
        fd = self.directory()
        if fd is None:
            return None
        file = None
        try:
            try:
                file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            except FileNotFoundError:
                return None
            info = os.fstat(file)
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 256 * 1024:
                return None
            raw = os.read(file, 256 * 1024 + 1)
            return raw if 0 < len(raw) <= 256 * 1024 else None
        finally:
            if file is not None:
                os.close(file)
            os.close(fd)

    def delete(self, locator):
        if locator is None:
            return
        name = self.name(locator)
        fd = self.directory()
        if fd is None:
            return
        try:
            for entry in (name, name + ".tmp"):
                try:
                    os.unlink(entry, dir_fd=fd)
                except FileNotFoundError:
                    pass
            os.fsync(fd)
        finally:
            os.close(fd)
