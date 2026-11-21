"""Persistente Jobs und pro-Asset-Verlauf. Kein automatisches Löschen defekter State-Dateien."""
from __future__ import annotations
import json
import os
from pathlib import Path
from dataclasses import asdict
from .models import Job, atomic_json


class FolderLock:
    def __init__(self, root: Path):
        self.path = Path(root) / '.assetfoundry' / 'batch.lock'
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0)
                if not self.file.read(1):
                    self.file.write(b'0'); self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            self.file.close()
            raise RuntimeError('Diesen Asset-Ordner verarbeitet bereits eine andere Instanz.') from None
        return self

    def __exit__(self, *args):
        if self.file:
            try:
                if os.name == 'nt':
                    import msvcrt
                    self.file.seek(0)
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            finally:
                self.file.close()


class JobStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.path = self.root / '.assetfoundry' / 'jobs.json'
        self.jobs: dict[str, Job] = {}
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            if raw.get('version') != 1 or not isinstance(raw.get('jobs'), dict):
                raise ValueError('Unbekannte oder beschädigte AssetFoundry-Jobdatei.')
            self.jobs = {k: Job.from_dict(v) for k, v in raw['jobs'].items()}

    def save(self):
        atomic_json(self.path, {'version': 1,
                                'jobs': {k: asdict(j) for k, j in self.jobs.items()}})

    def put(self, job: Job):
        self.jobs[job.rel] = job
        self.save()


class RunStore:
    """Small crash-resistant timeline of the most recent batch in an asset tree.

    Kept separately from jobs.json so previously installed versions can still read
    their original job files. User files are never removed or rewritten here.
    """
    def __init__(self, root: Path):
        self.path = Path(root).resolve() / '.assetfoundry' / 'last-run.json'

    def load(self):
        if not self.path.is_file():
            return {}
        raw = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(raw, dict) or raw.get('version') != 1:
            raise ValueError('Unbekannte oder beschädigte Batch-Zeitdatei.')
        if not isinstance(raw.get('rels', []), list):
            raise ValueError('Beschädigte Batch-Assetliste.')
        return raw

    def save(self, data: dict):
        atomic_json(self.path, {'version': 1, **data})
