"""Systemmetriken (psutil optional) und nicht-invasives Drosseln von NEUEN Jobs."""
from __future__ import annotations
import csv
import io
import shutil
import subprocess
import time
from dataclasses import dataclass
from threading import Event
from .models import Settings

try:
    import psutil
except ImportError:
    psutil = None


@dataclass
class Metrics:
    cpu: float | None = None
    ram: float | None = None
    gpu: float | None = None
    vram: float | None = None
    gpu_name: str = ''
    vram_used_mib: int | None = None
    vram_total_mib: int | None = None
    warning: str = ''


class ResourceMonitor:
    def __init__(self, gpu_index=0):
        self.gpu_index = gpu_index
        self._last_gpu = 0
        self._gpu_cache = None
        if psutil:
            psutil.cpu_percent(interval=None)

    def sample(self) -> Metrics:
        result = Metrics()
        if psutil:
            result.cpu = psutil.cpu_percent(interval=0.1)
            result.ram = psutil.virtual_memory().percent
        else:
            result.warning = 'Für CPU/RAM-Metriken psutil installieren (Start.bat).'
        # Kein globaler CUDA-Kontext wird geöffnet: kein zusätzlicher VRAM-Verbrauch.
        now = time.monotonic()
        if now - self._last_gpu < 2 and self._gpu_cache:
            gpu = self._gpu_cache
        else:
            gpu = self._query_nvidia()
            self._last_gpu = now
            self._gpu_cache = gpu
        if gpu:
            result.gpu, result.vram, result.gpu_name, result.vram_used_mib, result.vram_total_mib = gpu
        else:
            result.warning += (' ' if result.warning else '') + 'GPU-Metriken nicht verfügbar (NVIDIA-SMI).'
        return result

    def _query_nvidia(self):
        if not shutil.which('nvidia-smi'):
            return None
        try:
            p = subprocess.run(['nvidia-smi', '--query-gpu=index,name,utilization.gpu,memory.used,memory.total',
                                '--format=csv,noheader,nounits'], capture_output=True, text=True,
                               timeout=3, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            rows = list(csv.reader(io.StringIO(p.stdout)))
            for r in rows:
                if len(r) >= 5 and int(r[0].strip()) == self.gpu_index:
                    used, total = int(r[3].strip()), int(r[4].strip())
                    return float(r[2].strip()), round(used / total * 100, 1) if total else 0, r[1].strip(), used, total
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        return None


def overload(metrics: Metrics, s: Settings) -> list[str]:
    if not s.gate_resources:
        return []
    checks = [('CPU', metrics.cpu, s.cpu_limit), ('RAM', metrics.ram, s.ram_limit),
              ('GPU', metrics.gpu, s.gpu_limit), ('VRAM', metrics.vram, s.vram_limit)]
    return [f'{k} {val:.0f}% ≥ {limit}%' for k, val, limit in checks if val is not None and val >= limit]


def wait_for_resources(monitor: ResourceMonitor, settings: Settings, stop: Event,
                       notify=lambda x: None, now=time.monotonic):
    """Gated entry: Zeitüberschreitung wird zum Fehler, nicht stillschweigend ignoriert."""
    begun = now()
    previous = ''
    while True:
        if stop.is_set():
            return False
        metrics = monitor.sample()
        reasons = overload(metrics, settings)
        if not reasons:
            return True
        msg = 'Warte wegen Systemlast: ' + ', '.join(reasons)
        if msg != previous:
            notify(msg)
            previous = msg
        if now() - begun > settings.max_resource_wait_seconds:
            raise TimeoutError('Systemlast blieb zu hoch; Batch sicher pausiert.')
        stop.wait(2)


def cooldown(seconds: float, stop: Event, notify=lambda x: None):
    if seconds <= 0:
        return not stop.is_set()
    notify(f'Cooldown zwischen Generierungen: {seconds:g} Sekunden')
    return not stop.wait(seconds)
