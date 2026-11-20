"""GLB-Strukturprüfung und verlustarmer, atomarer Dateiaustausch."""
from __future__ import annotations
import contextlib
import json
import os
import shutil
import struct
import tempfile
import time
from pathlib import Path


def validate_glb(path) -> bool:
    """Prüft glTF-2.0-Container, Chunk-Grenzen und minimale glTF-Struktur."""
    try:
        p = Path(path)
        size = p.stat().st_size
        if size < 20 or size > 4 * 1024 ** 3 or p.is_symlink():
            return False
        with p.open('rb') as f:
            head = f.read(20)
            magic, version, total, length, kind = struct.unpack('<IIIII', head)
            if (magic, version, total, kind) != (0x46546C67, 2, size, 0x4E4F534A):
                return False
            if length % 4 or length > size - 20 or length > 64 * 1024 ** 2:
                return False
            doc = json.loads(f.read(length))
            if doc.get('asset', {}).get('version') != '2.0' or not isinstance(doc, dict):
                return False
            if not (isinstance(doc.get('meshes'), list) or isinstance(doc.get('nodes'), list)
                    or isinstance(doc.get('scenes'), list)):
                return False
            while f.tell() < size:
                ch = f.read(8)
                if len(ch) != 8:
                    return False
                n, kind = struct.unpack('<II', ch)
                if n % 4 or n > size - f.tell() or kind not in (0x004E4942, 0x4E4F534A):
                    return False
                f.seek(n, 1)
            return f.tell() == size
    except (OSError, ValueError, TypeError, AttributeError, struct.error):
        return False


class OutputManager:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def publish(self, target: Path, source_stream, replace=False, backup=True,
                size_limit=4 * 1024 ** 3) -> Path:
        """Streamt GLB in denselben Zielordner, validiert, erstellt optional Backup.

        Non-replace: exklusives Publish. Replace: os.replace erst nach vollständiger Validierung.
        """
        target = Path(target)
        if target.is_symlink() or not target.parent.resolve().is_relative_to(self.root):
            raise ValueError('Unsicheres Ziel ausserhalb des Asset-Ordners.')
        if target.suffix.lower() != '.glb':
            raise ValueError('Ziel muss eine GLB-Datei sein.')
        if not replace and target.exists():
            raise FileExistsError('GLB existiert bereits: kein Überschreiben erlaubt.')
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode='wb', dir=target.parent,
                                             prefix='.assetfoundry-', suffix='.part',
                                             delete=False) as f:
                tmp = Path(f.name)
                count = 0
                while True:
                    chunk = source_stream.read(256 * 1024)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > size_limit:
                        raise ValueError('GLB überschreitet das erlaubte Grössenlimit.')
                    f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
            if not validate_glb(tmp):
                raise ValueError('ComfyUI lieferte keine gültige glTF-2.0-GLB.')
            if target.is_symlink():
                raise ValueError('GLB-Ziel ist zwischenzeitlich zu einem Link geworden.')
            if replace:
                if target.exists() and backup:
                    self._backup(target)
                os.replace(tmp, target)
            elif os.name == 'nt':
                # Windows os.rename fails when destination exists (unlike os.replace).
                os.rename(tmp, target)
            else:
                os.link(tmp, target)  # exklusiver Name ohne TOCTOU-Überschreiben
            return target
        finally:
            if tmp is not None:
                tmp.unlink(missing_ok=True)

    def _backup(self, target: Path):
        rel = target.relative_to(self.root)
        folder = self.root / '.assetfoundry' / 'backups' / rel.parent
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        backup = folder / (target.stem + '.' + stamp + '.' + os.urandom(4).hex() + '.glb')
        shutil.copy2(target, backup)
        with backup.open('rb') as f:
            os.fsync(f.fileno())
