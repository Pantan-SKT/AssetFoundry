"""Local, authenticated dashboard controller, independent of any HTML/UI toolkit.

The production engine remains in batch.py; this module only manages UI requests,
selection, cached scans, telemetry, and human-readable status.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
import threading
import time

from . import __version__
from .assets import scan
from .batch import BatchRunner
from .comfy import ComfyClient
from .models import Settings, load_settings, save_settings, settings_file
from .resources import ResourceMonitor, Metrics
from .state import JobStore
from .workflow import Workflow

BASE = Path(__file__).resolve().parent.parent
ERROR_STATES = {'fehler', 'ungültige GLB', 'gesperrt', 'unklar', 'bild-geändert', 'ausgabe-fehler', 'zeitlimit'}


class DashboardController:
    def __init__(self, settings: Settings | None = None, auto_scan=True, monitor=True):
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.last_error = ''
        self._scanned_once = False
        self.settings = settings if settings is not None else self._load_settings()
        self.assets = []
        self.asset_index = {}
        self.selected: set[str] = set()
        self.store = None
        self.runner: BatchRunner | None = None
        self.scanning = False
        self.connection: str = 'unknown'
        self.last_metrics = Metrics()
        self.recent = deque(maxlen=300)
        self.batch_rels: set[str] = set()
        self.batch_started_at = 0.0
        self.replace_mode = False
        if not self.settings.workflow_path or not Path(self.settings.workflow_path).is_file():
            for name in ('API.json', 'api.json', 'workflow_api.json'):
                candidate = BASE / 'workflows' / name
                if candidate.is_file():
                    self.settings.workflow_path = str(candidate)
                    break
        self.log('AssetFoundry gestartet. Warte auf Ordner und ComfyUI-Workflow.')
        if auto_scan and self.settings.root and Path(self.settings.root).is_dir():
            self.start_scan(self.settings.root)
        if monitor:
            threading.Thread(target=self._monitor_loop, daemon=True, name='assetfoundry-telemetry').start()

    def _load_settings(self) -> Settings:
        try:
            return load_settings()
        except Exception as exc:
            self.last_error = f'Einstellungen konnten nicht gelesen werden: {exc}'
            return Settings()

    def log(self, message: str, level='info'):
        timestamp = time.time()
        with self.lock:
            self.recent.append({'time': timestamp, 'level': level, 'message': str(message)[:1200]})
            if level == 'error':
                self.last_error = str(message)[:1200]
        try:
            p = settings_file().parent / 'activity.log'
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists() and p.stat().st_size > 3 * 1024 * 1024:
                p.replace(p.with_suffix('.previous.log'))
            with p.open('a', encoding='utf-8') as f:
                f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + f'[{level}] ' + str(message)[:1200] + '\n')
        except OSError:
            pass

    @property
    def running(self) -> bool:
        return bool(self.runner and self.runner.running)

    def start_scan(self, root: str):
        path = Path(root).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise ValueError('Der ausgewählte Asset-Ordner existiert nicht.')
        with self.lock:
            if self.running:
                raise RuntimeError('Während des Batches kann der Ordner nicht gewechselt werden.')
            if self.scanning:
                raise RuntimeError('Der Scanner läuft bereits.')
            self.scanning = True
        def worker():
            try:
                result = scan(path)
                store = JobStore(path)  # damaged state must not be silently replaced
                with self.lock:
                    same_root = str(path) == self.settings.root
                    previous = self.selected.copy() if same_root else set()
                    select_defaults = not (self._scanned_once and same_root)
                    self.assets = result
                    self.asset_index = {a.rel: a for a in result}
                    self.selected = (previous & set(self.asset_index)) if same_root else set()
                    if select_defaults:
                        self.selected = {a.rel for a in result if not a.target.exists() and a.status != 'gesperrt'}
                    self._scanned_once = True
                    self.store = store
                    self.settings.root = str(path)
                    save_settings(self.settings)
                self.log(f'{len(result)} PNGs rekursiv eingelesen · {sum(a.target.exists() for a in result)} GLBs vorhanden.')
            except Exception as exc:
                self.log('Scan fehlgeschlagen: ' + str(exc), 'error')
            finally:
                with self.lock:
                    self.scanning = False
        threading.Thread(target=worker, name='assetfoundry-scan', daemon=True).start()
        return {'accepted': True}

    def _job(self, rel):
        store = self.runner.store if self.runner and self.runner.root == Path(self.settings.root).resolve() else self.store
        return store.jobs.get(rel) if store else None

    def _asset_status(self, a):
        if a.status in ('gesperrt', 'ungültige GLB'):
            return a.status, a.message or ('Ungültiges Zielmodell' if a.status == 'ungültige GLB' else 'Gesperrt')
        if a.target.exists():
            return 'vorhanden', 'GLB liegt im Asset-Ordner'
        job = self._job(a.rel)
        if job and job.status not in ('fertig', 'bereit'):
            return job.status, job.message
        return 'bereit', 'Noch kein GLB vorhanden'

    def _filter(self, search='', mode='all'):
        q = str(search).strip().casefold()
        output = []
        for a in self.assets:
            if q and q not in a.rel.casefold():
                continue
            status, note = self._asset_status(a)
            if mode == 'missing' and a.target.exists():
                continue
            if mode == 'existing' and not a.target.exists():
                continue
            if mode == 'errors' and status not in ERROR_STATES:
                continue
            if mode == 'complete' and status not in ('vorhanden', 'fertig'):
                continue
            output.append((a, status, note))
        return output

    def snapshot(self, search='', mode='all', page=0, page_size=100):
        page_size = min(max(int(page_size), 1), 200)
        page = max(int(page), 0)
        with self.lock:
            matches = self._filter(search, mode)
            assets = matches[page*page_size:(page+1)*page_size]
            total = len(self.assets)
            present = sum(a.target.exists() for a in self.assets)
            blocked = sum(a.status == 'gesperrt' for a in self.assets)
            done = sum(1 for rel in self.batch_rels if self._job(rel)
                       and self._job(rel).status == 'fertig'
                       and self._job(rel).finished >= self.batch_started_at)
            metrics = asdict(self.last_metrics)
            return {'version': __version__, 'settings': asdict(self.settings), 'scanning': self.scanning,
                    'running': self.running, 'connection': self.connection,
                    'metrics': metrics, 'error': self.last_error,
                    'counts': {'total': total, 'existing': present, 'missing': total-present,
                               'selected': len(self.selected), 'selected_existing': sum(a.target.exists() for a in self.assets if a.rel in self.selected),
                               'selected_missing': sum(not a.target.exists() for a in self.assets if a.rel in self.selected), 'blocked': blocked,
                               'batch_total': len(self.batch_rels), 'batch_done': done,
                               'filtered': len(matches)},
                    'assets': [{'rel': a.rel, 'status': status, 'note': note,
                                'selected': a.rel in self.selected, 'has_glb': a.target.exists(),
                                'size': a.source_size} for a, status, note in assets],
                    'page': page, 'page_size': page_size,
                    'logs': list(self.recent)[-90:]}

    def toggle(self, rel: str, selected: bool):
        with self.lock:
            a = self.asset_index.get(rel)
            if not a:
                raise ValueError('Das Asset ist nicht mehr in der aktuellen Liste.')
            if self.running or self.scanning:
                raise RuntimeError('Auswahl während des Batches/Scans gesperrt.')
            if selected and a.status == 'gesperrt':
                raise ValueError('Gesperrte Assets können nicht verarbeitet werden.')
            if selected:
                self.selected.add(rel)
            else:
                self.selected.discard(rel)
            return {'selected': len(self.selected)}

    def select(self, mode, search='', filt='all'):
        with self.lock:
            if self.running or self.scanning:
                raise RuntimeError('Auswahl während eines aktiven Batches/Scans gesperrt.')
            if mode == 'none':
                self.selected.clear()
            elif mode == 'missing':
                self.selected = {a.rel for a in self.assets if not a.target.exists() and a.status != 'gesperrt'}
            elif mode == 'visible':
                self.selected.update(a.rel for a, _, _ in self._filter(search, filt) if a.status != 'gesperrt')
            else:
                raise ValueError('Unbekannter Auswahlmodus.')
            return {'selected': len(self.selected)}

    def _new_settings(self, updates: dict):
        if not isinstance(updates, dict):
            raise ValueError('Einstellungen müssen ein Objekt sein.')
        editable = {'comfy_url', 'cooldown_seconds', 'cpu_limit', 'ram_limit', 'gpu_limit',
                    'vram_limit', 'gpu_index', 'gate_resources', 'max_resource_wait_seconds',
                    'max_job_seconds', 'poll_seconds', 'backup_on_replace'}
        if set(updates) - editable:
            raise ValueError('Unbekannte oder nicht änderbare Einstellung.')
        obj = asdict(self.settings)
        for k, v in updates.items():
            if k in {'cpu_limit','ram_limit','gpu_limit','vram_limit','gpu_index',
                     'max_resource_wait_seconds','max_job_seconds'}:
                if isinstance(v, bool): raise ValueError(k + ': Zahl erwartet.')
                v = int(v)
            if k in {'cooldown_seconds','poll_seconds'}:
                v = float(v)
            if k in {'gate_resources','backup_on_replace'} and not isinstance(v, bool):
                raise ValueError(k + ': Boolean erwartet.')
            obj[k] = v
        s = Settings(**obj)
        s.validate()
        return s

    def update_settings(self, updates):
        with self.lock:
            if self.running:
                raise RuntimeError('Einstellungen während des Batches nicht verändern.')
            s = self._new_settings(updates)
            save_settings(s)
            self.settings = s
            self.connection = 'unknown'
        self.log('Leistungseinstellungen gespeichert · Cooldown ' + f'{s.cooldown_seconds:g} s')
        return {'ok': True}

    def inspect_workflow(self, path: str):
        workflow = Workflow(path)
        images, outputs = workflow.image_options(), workflow.output_options()
        return {'nodes': len(workflow.graph), 'images': [asdict(x) for x in images],
                'outputs': [asdict(x) for x in outputs], 'path': str(workflow.path)}

    def save_workflow(self, data):
        if not isinstance(data, dict): raise ValueError('Ungültige Workflow-Einstellungen.')
        with self.lock:
            if self.running: raise RuntimeError('Workflow nicht während eines aktiven Batches ändern.')
            s = Settings(**asdict(self.settings))
            for field in ('workflow_path','image_node','output_node','image_input','prefix_input'):
                if field in data:
                    value = data[field]
                    if not isinstance(value, str): raise ValueError(field + ': Text erwartet.')
                    setattr(s, field, value.strip())
            Workflow(s.workflow_path).resolve(s.image_node, s.output_node, s.image_input, s.prefix_input)
            save_settings(s)
            self.settings = s
        self.log('ComfyUI-API-Workflow geprüft und zugeordnet: ' + Path(s.workflow_path).name)
        return {'ok': True}

    def test_connection(self):
        with self.lock:
            url = self.settings.comfy_url
            self.connection = 'checking'
        def worker():
            try:
                settings = Settings(**asdict(self.settings))
                settings.comfy_url = url
                ComfyClient(settings, timeout=8).ping()
                with self.lock: self.connection = 'online'
                self.log('ComfyUI-Verbindung erfolgreich: ' + url)
            except Exception as exc:
                with self.lock: self.connection = 'offline'
                self.log('ComfyUI nicht erreichbar: ' + str(exc), 'error')
        threading.Thread(target=worker, name='assetfoundry-connect', daemon=True).start()
        return {'accepted': True}

    def _notify(self, kind, message):
        self.log(message, 'error' if kind == 'error' else 'info')
        if kind == 'finished':
            # Scan again only when the worker has actually stopped (race-safe).
            def rescan():
                for _ in range(100):
                    if not self.running and not self.closed.is_set():
                        try: self.start_scan(self.settings.root)
                        except Exception as exc: self.log(str(exc), 'error')
                        return
                    time.sleep(0.1)
            threading.Thread(target=rescan, daemon=True).start()

    def start_batch(self, replace=False):
        if not isinstance(replace, bool): raise ValueError('Ungültiger Überschreibmodus.')
        with self.lock:
            if self.running or self.scanning:
                raise RuntimeError('Eine Verarbeitung oder ein Scan läuft bereits.')
            if not self.settings.root or not self.asset_index:
                raise ValueError('Bitte zuerst einen Ordner mit PNG-Dateien scannen.')
            rels = sorted(self.selected)
            if not rels:
                raise ValueError('Bitte Assets auswählen.')
            runner = BatchRunner(Settings(**asdict(self.settings)), self._notify)
            runner.start(rels, replace=replace)
            self.runner = runner
            self.batch_rels = set(rels)
            self.batch_started_at = time.time()
            self.replace_mode = replace
        self.log(f'Batch gestartet: {len(rels)} ausgewählt · ' + ('Überschreiben bestätigt' if replace else 'Bestehende GLBs überspringen'))
        return {'accepted': True}

    def pause(self):
        with self.lock:
            if not self.running: raise RuntimeError('Kein Batch läuft.')
            self.runner.pause()
        return {'accepted': True}

    def reset_selected(self):
        with self.lock:
            if self.running or self.scanning: raise RuntimeError('Zuerst Batch/Scan beenden.')
            if not self.selected: raise ValueError('Keine Assets ausgewählt.')
            if not self.settings.root: raise ValueError('Kein Asset-Ordner.')
            runner = self.runner if self.runner and self.runner.root == Path(self.settings.root).resolve() else BatchRunner(self.settings, self._notify)
            runner.reset(sorted(self.selected))
            self.store = runner.store
        self.log('Ausgewählte Jobs wurden explizit zurückgesetzt (GLBs nicht gelöscht).')
        return {'ok': True}

    def preview(self, rel: str) -> tuple[bytes, str]:
        with self.lock:
            a = self.asset_index.get(rel)
            if not a: raise FileNotFoundError('Asset nicht im aktuellen Ordner.')
            path = a.image
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError('Asset nicht verfügbar.')
        from .assets import validate_png
        validate_png(path)
        try:
            from PIL import Image, ImageOps
            # Safeguard against pathological images. Invalid previews never affect the batch.
            with Image.open(path) as src:
                if src.width * src.height > 64_000_000:
                    raise ValueError('Bild zu gross für Vorschau.')
                src.thumbnail((900, 900))
                rgb = ImageOps.exif_transpose(src).convert('RGB')
                result = BytesIO()
                rgb.save(result, format='JPEG', quality=83)
                return result.getvalue(), 'image/jpeg'
        except ImportError:
            if path.stat().st_size <= 10 * 1024 * 1024:
                return path.read_bytes(), 'image/png'
            raise ValueError('Für grosse Vorschaubilder wird Pillow benötigt.')

    def _monitor_loop(self):
        monitor, idx = None, None
        while not self.closed.is_set():
            try:
                new_idx = self.settings.gpu_index
                if idx != new_idx or monitor is None:
                    monitor, idx = ResourceMonitor(new_idx), new_idx
                sample = monitor.sample()
                with self.lock: self.last_metrics = sample
            except Exception as exc:
                self.log('Systemmonitoring: ' + str(exc), 'error')
            self.closed.wait(2.5)

    def close(self):
        self.closed.set()
        if self.running:
            self.runner.pause()
