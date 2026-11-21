"""On-demand, bounded in-memory PNG thumbnails for the local asset library.

Only paths resolved by DashboardController's scanned asset index are accepted.
No background pre-generation, disk cache, or remote requests.
"""
from __future__ import annotations

from collections import OrderedDict
from io import BytesIO
from pathlib import Path
from threading import RLock
from struct import unpack

from .assets import validate_png


class ThumbnailService:
    """Create 128px PNG previews, caching up to a fixed memory budget."""

    def __init__(self, pixels: int = 128, max_bytes: int = 16 * 1024 * 1024,
                 max_entries: int = 256):
        self.pixels = pixels
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self._cache: OrderedDict[tuple[str, int, int], bytes] = OrderedDict()
        self._bytes = 0
        self._lock = RLock()

    def get(self, path: Path) -> tuple[bytes, str]:
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError('PNG nicht mehr verfügbar.')
        validate_png(path)
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key], 'image/png'

        try:
            from PIL import Image, ImageOps, UnidentifiedImageError
        except ImportError:
            # Avoid shipping the full original unless it is already physically tiny.
            # Tiny compressed PNGs can still decode to huge bitmaps: inspect IHDR too.
            with path.open('rb') as file:
                header = file.read(24)
            if len(header) == 24 and header[12:16] == b'IHDR':
                width, height = unpack('>II', header[16:24])
                if stat.st_size <= 192 * 1024 and 0 < width <= 256 and 0 < height <= 256:
                    return path.read_bytes(), 'image/png'
            raise RuntimeError('Für PNG-Thumbnails wird Pillow benötigt. Extras_Installieren.bat ausführen.')

        try:
            with Image.open(path) as original:
                if original.format != 'PNG' or original.width * original.height > 64_000_000:
                    raise ValueError('PNG zu gross oder ungültig für ein Thumbnail.')
                # thumbnail() reduces work before conversion, preserving alpha and aspect ratio.
                original.thumbnail((self.pixels, self.pixels), Image.Resampling.LANCZOS)
                small = ImageOps.exif_transpose(original).convert('RGBA')
                buffer = BytesIO()
                small.save(buffer, 'PNG', optimize=True)
                content = buffer.getvalue()
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise ValueError('PNG konnte nicht als Thumbnail gelesen werden.') from exc

        with self._lock:
            # Avoid keeping multiple cached variants of the same PNG after an edit.
            for old_key in list(self._cache):
                if old_key[0] == str(path) and old_key != key:
                    self._bytes -= len(self._cache.pop(old_key))
            if key in self._cache:  # Another request may have completed while decoding.
                self._cache.move_to_end(key)
                return self._cache[key], 'image/png'
            if len(content) <= self.max_bytes:
                self._cache[key] = content
                self._bytes += len(content)
                while len(self._cache) > self.max_entries or self._bytes > self.max_bytes:
                    _, oldest = self._cache.popitem(last=False)
                    self._bytes -= len(oldest)
        return content, 'image/png'
