"""Bounded, explicit local support export. No PNG, GLB, API JSON or credentials.

The export is generated in-memory, only after a user presses the export button.
Absolute paths are replaced by placeholders by default. Relative filenames can
still occur in error messages: inspect a ZIP before sharing it publicly.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import platform
import re
import sys
import time
from zipfile import ZipFile, ZIP_DEFLATED

from . import __version__
from .models import settings_file

# Diagnostic archives must not grow with asset count. This limit is large
# enough for actionable reports without processing arbitrarily big libraries.
MAX_JOBS = 10_000
MAX_FILE_BYTES = 700_000
MAX_RECENT = 300


def _tail(path: Path, size: int = MAX_FILE_BYTES) -> str:
    try:
        with path.open('rb') as source:
            source.seek(0, 2)
            length = source.tell()
            source.seek(max(0, length - size))
            data = source.read(size)
        if length > size:
            data = data.partition(b'\n')[2]
        return data.decode('utf-8', errors='replace')
    except OSError as exc:
        return f'Logdatei nicht lesbar: {exc.__class__.__name__}'


def _mask_text(text: str, paths: list[str]) -> str:
    for path in sorted((v for v in paths if v), key=len, reverse=True):
        text = text.replace(path, '<PFAD>')
        text = text.replace(path.replace('\\', '/'), '<PFAD>')
        text = text.replace(path.replace('/', '\\'), '<PFAD>')
    # Mask common user profile roots independently of the configured asset root.
    text = re.sub(r'(?i)\b[A-Z]:[\\/](?:Users|Benutzer)[\\/][^\\/\s]+', r'<BENUTZERPFAD>', text)
    text = re.sub(r'/(?:home|Users)/[^/\s]+', '<BENUTZERPFAD>', text)
    # Prevent accidental credential disclosure from unusual ComfyUI error messages.
    text = re.sub(r'(?i)\b(authorization|api[_-]?key|password|secret|token)\s*[:=]\s*[\'\"]?[^\s,\'\"]+',
                  r'\1=<GESCHUETZT>', text)
    return text


def _sanitize_data(obj, paths, include_paths):
    if isinstance(obj, str):
        return obj if include_paths else _mask_text(obj, paths)
    if isinstance(obj, list):
        return [_sanitize_data(x, paths, include_paths) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _sanitize_data(v, paths, include_paths) for k, v in obj.items()}
    return obj


def build_diagnostics(controller, include_paths: bool = False) -> bytes:
    if not isinstance(include_paths, bool):
        raise ValueError('Ungültige Dump-Option.')
    with controller.lock:
        settings = asdict(controller.settings)
        metrics = asdict(controller.last_metrics)
        recent = list(controller.recent)[-MAX_RECENT:]
        error = controller.last_error
        root = controller.settings.root
        workflow = controller.settings.workflow_path
        selection = len(controller.selected)
        run = {
            'started_at': controller.batch_started_at,
            'ended_at': controller.batch_finished_at,
            'heartbeat_at': controller.batch_heartbeat_at,
            'status': 'running' if controller.running else controller.batch_outcome,
            'assets_selected_at_start': len(controller.batch_rels),
            'current_selection_count': selection,
            'overwrite': controller.replace_mode,
        }
        store = controller.store
        if root and controller.runner and controller.runner.root == Path(root).resolve():
            store = controller.runner.store
        job_items = list(store.jobs.items())[-MAX_JOBS:] if store is not None else []
        asset_count = len(controller.assets)
        connection = controller.connection
        scanning = controller.scanning
    # The output is deliberately a snapshot: archive creation never mutates job state.
    config = {k: v for k, v in settings.items() if k not in ('root', 'workflow_path')}
    if include_paths:
        config['root'] = root
        config['workflow_path'] = workflow
    else:
        config['root'] = '<ASSET-ORDNER>' if root else ''
        config['workflow_path'] = Path(workflow).name if workflow else ''
    paths = [root, workflow, str(Path.home()), os.environ.get('USERPROFILE', ''), str(settings_file().parent)]
    if root:
        paths.append(str(Path(root).resolve()))
    metadata = {
        'name': 'AssetFoundry Diagnose-Dump', 'version': __version__,
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'privacy': 'vollstaendige_pfade' if include_paths else 'absolute_pfade_verdeckt',
        'platform': platform.platform(), 'python': sys.version.split()[0],
        'connection': connection, 'scanning': scanning,
        'settings': config, 'resources': metrics, 'last_error': error,
        'batch': run, 'assets_scanned': asset_count,
        'job_count_total': len(store.jobs) if store is not None else 0,
        'jobs_in_archive': len(job_items),
    }
    jobs = []
    for rel, job in job_items:
        jobs.append({'asset': rel, 'state': job.status, 'message': job.message,
                     'prompt_id': job.prompt_id, 'started': job.started, 'finished': job.finished,
                     'workflow_hash': job.workflow_hash, 'output_node': job.output_node,
                     'history': job.history[-25:] if isinstance(job.history, list) else [],
                     'replace': job.replace})
    appdata = settings_file().parent
    activity = _tail(appdata / 'activity.log')
    errors = _tail(appdata / 'errors.log')
    recent_json = json.dumps(_sanitize_data(recent, paths, include_paths), ensure_ascii=False, indent=2)
    contents = {
        'diagnose.json': json.dumps(_sanitize_data(metadata, paths, include_paths), indent=2, ensure_ascii=False),
        'jobs.json': json.dumps(_sanitize_data(jobs, paths, include_paths), indent=2, ensure_ascii=False),
        'recent_events.json': recent_json,
        'activity.log': activity,
        'errors.log': errors,
        'LIESMICH.txt': (
            'AssetFoundry Diagnose-Dump\n'
            '------------------------\n'
            'Enthaelt: Umgebung, Systemlast, Einstellungen, Batch-Zeiten, Jobstatus,\n'
            'ComfyUI-Prompt-IDs, Fehlermeldungen und lokale Ereignisprotokolle.\n'
            'Enthaelt NICHT: PNG, GLB, API-Workflow-JSON, Auth-Token oder API-Schluessel.\n'
            'Es koennen dennoch Dateinamen und Text aus Fehlermeldungen enthalten sein.\n'
            'Bitte den Inhalt vor dem Weitergeben pruefen.\n'
            f'Pfadschutz: {"aus (vollstaendige Pfade)" if include_paths else "an (absolute Pfade verdeckt)"}\n'
        ),
    }
    buffer = BytesIO()
    with ZipFile(buffer, 'w', compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for name, content in contents.items():
            content = _sanitize_data(content, paths, include_paths)
            # Credentials are masked even when the user opts into full paths.
            content = re.sub(r'(?i)\b(authorization|api[_-]?key|password|secret|token)\s*[:=]\s*[\'\"]?[^\s,\'\"]+',
                             r'\1=<GESCHUETZT>', content)
            archive.writestr(name, content.encode('utf-8'))
    return buffer.getvalue()
