"""Datentypen und lokale Konfiguration; keine Abhängigkeit von UI/ComfyUI."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from pathlib import Path
import json
import os
import tempfile


@dataclass
class Settings:
    root: str = ''
    comfy_url: str = 'http://127.0.0.1:8188'
    workflow_path: str = ''
    image_node: str = ''
    image_input: str = 'image'
    output_node: str = ''
    prefix_input: str = 'filename_prefix'
    cooldown_seconds: float = 5.0
    cpu_limit: int = 90
    ram_limit: int = 90
    gpu_limit: int = 95
    vram_limit: int = 95
    gpu_index: int = 0
    gate_resources: bool = True
    max_resource_wait_seconds: int = 1800
    max_job_seconds: int = 3600
    poll_seconds: float = 3.0
    backup_on_replace: bool = True

    def validate(self):
        from urllib.parse import urlparse
        u = urlparse(self.comfy_url.strip())
        if (u.scheme != 'http' or u.hostname not in ('localhost', '127.0.0.1', '::1')
                or u.username or u.password or u.path not in ('', '/') or u.query or u.fragment):
            raise ValueError('ComfyUI-URL muss lokal sein, z.B. http://127.0.0.1:8188')
        if u.port is not None and not 1 <= u.port <= 65535:
            raise ValueError('Ungültiger ComfyUI-Port')
        if not 0 <= self.cooldown_seconds <= 3600:
            raise ValueError('Cooldown muss zwischen 0 und 3600 Sekunden liegen.')
        for key in ('cpu_limit', 'ram_limit', 'gpu_limit', 'vram_limit'):
            if not 1 <= getattr(self, key) <= 100:
                raise ValueError(key + ' muss zwischen 1 und 100 liegen.')
        if not 0 <= self.gpu_index <= 128:
            raise ValueError('Ungültiger GPU-Index.')
        if not 30 <= self.max_job_seconds <= 86400:
            raise ValueError('Maximale Job-Dauer muss 30 bis 86400 Sekunden sein.')
        if not 10 <= self.max_resource_wait_seconds <= 86400:
            raise ValueError('Maximale Lastwartezeit muss 10 bis 86400 Sekunden sein.')
        if not 0.5 <= self.poll_seconds <= 60:
            raise ValueError('Polling-Intervall muss 0.5 bis 60 Sekunden sein.')


def settings_file() -> Path:
    base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / '.config') / 'AssetFoundry'
    return base / 'settings.json'


def atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                         prefix='.assetfoundry-', suffix='.tmp', delete=False) as f:
            tmp = Path(f.name)
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def load_settings(path: Path | None = None) -> Settings:
    p = path or settings_file()
    if not p.exists():
        return Settings()
    obj = json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(obj, dict):
        raise ValueError('Die Einstellungen sind beschädigt.')
    keys = Settings.__dataclass_fields__
    s = Settings(**{k: v for k, v in obj.items() if k in keys})
    s.validate()
    return s


def save_settings(s: Settings, path: Path | None = None):
    s.validate()
    atomic_json(path or settings_file(), asdict(s))


@dataclass
class Asset:
    rel: str
    image: Path
    target: Path
    status: str = 'bereit'
    selected: bool = False
    message: str = ''
    source_size: int = 0
    source_mtime_ns: int = 0


@dataclass
class Job:
    rel: str
    fingerprint: str
    replace: bool = False
    status: str = 'bereit'
    prompt_id: str = ''
    output_node: str = ''
    workflow_hash: str = ''
    message: str = ''
    started: float = 0
    finished: float = 0
    history: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
