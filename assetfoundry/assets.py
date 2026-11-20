"""Rekursiver PNG-Scanner und sicheres Ziel-Mapping."""
from __future__ import annotations
from pathlib import Path
import hashlib
import os
from .models import Asset
from .output import validate_glb


PNG_MAGIC = b'\x89PNG\r\n\x1a\n'


def fingerprint(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        while True:
            data = f.read(1024 * 1024)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


def validate_png(path: Path):
    if not path.is_file() or path.is_symlink():
        raise ValueError('Bild fehlt oder ist ein symbolischer Link.')
    if path.stat().st_size > 100 * 1024 * 1024:
        raise ValueError('PNG grösser als 100 MiB.')
    with path.open('rb') as f:
        if f.read(8) != PNG_MAGIC:
            raise ValueError('Ungültige PNG-Signatur.')


def scan(root: Path) -> list[Asset]:
    root = root.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Kein gültiger Hauptordner.')
    result = []
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted((d for d in dirs if d.lower() != '.assetfoundry' and
                           not (Path(folder) / d).is_symlink()), key=str.casefold)
        seen = {name.casefold(): name for name in files}
        for name in sorted(files, key=str.casefold):
            if Path(name).suffix.lower() != '.png':
                continue
            image = Path(folder) / name
            if image.is_symlink():
                continue
            target = image.with_suffix('.glb')
            rel = image.relative_to(root).as_posix()
            st = image.stat()
            # Windows is case-insensitive. If an existing GLB differs only in case, use that path.
            actual_glb = next((n for n in files if n.casefold() == target.name.casefold()), None)
            if actual_glb:
                target = Path(folder) / actual_glb
            status = 'bereit'
            message = ''
            if target.is_symlink():
                status, message = 'gesperrt', 'GLB-Ziel ist ein symbolischer Link.'
            elif target.exists():
                status = 'vorhanden' if validate_glb(target) else 'ungültige GLB'
            result.append(Asset(rel=rel, image=image, target=target, status=status,
                                message=message, source_size=st.st_size,
                                source_mtime_ns=st.st_mtime_ns))
    # Casefold collisions for case-insensitive destinations or asset keys.
    groups = {}
    for a in result:
        groups.setdefault(a.target.relative_to(root).as_posix().casefold(), []).append(a)
    for group in groups.values():
        if len(group) > 1:
            for a in group:
                a.status = 'gesperrt'
                a.message = 'Namenskollision: mehrere PNGs verweisen auf dasselbe GLB-Ziel.'
    return result
