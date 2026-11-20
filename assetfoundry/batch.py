"""Serielle Batch-Engine mit Wiederaufnahme, Payload-Fingerprints und Soft-Pause."""
from __future__ import annotations
import threading
import time
import uuid
from pathlib import Path
from typing import Callable
from .assets import scan, fingerprint, validate_png
from .comfy import ComfyClient, ComfyError, UncertainSubmission
from .models import Asset, Job, Settings
from .output import OutputManager, validate_glb
from .resources import ResourceMonitor, wait_for_resources, cooldown
from .state import FolderLock, JobStore
from .workflow import Workflow


class BatchRunner:
    def __init__(self, settings: Settings, notify: Callable[[str, str], None] | None = None,
                 client_factory=ComfyClient, monitor_factory=ResourceMonitor):
        self.settings = settings
        self.notify = notify or (lambda kind, msg: None)
        self.client_factory = client_factory
        self.monitor_factory = monitor_factory
        self.stop = threading.Event()
        self.worker: threading.Thread | None = None
        self.root = Path(settings.root).expanduser().resolve()
        self.store = JobStore(self.root)

    @property
    def running(self):
        return self.worker is not None and self.worker.is_alive()

    def start(self, selected_rels: list[str], replace=False):
        if self.running:
            raise RuntimeError('Batch läuft bereits.')
        if not selected_rels:
            raise ValueError('Keine Assets ausgewählt.')
        self.settings.validate()
        if not Path(self.settings.workflow_path).is_file():
            raise ValueError('ComfyUI API-Workflow fehlt. Bitte workflows/API.json hinterlegen und im Workflow-Tab prüfen.')
        Workflow(self.settings.workflow_path).resolve(self.settings.image_node,
                                                      self.settings.output_node,
                                                      self.settings.image_input,
                                                      self.settings.prefix_input)
        assets = {a.rel: a for a in scan(self.root)}
        if any(rel not in assets for rel in selected_rels):
            raise ValueError('Asset-Auswahl ist veraltet: bitte Ordner erneut scannen.')
        if any(assets[rel].status == 'gesperrt' for rel in selected_rels):
            raise ValueError('Ein Asset ist gesperrt (Symlink/Namenskollision).')
        self.stop.clear()
        self.worker = threading.Thread(target=self._run, args=(list(dict.fromkeys(selected_rels)), replace),
                                       name='assetfoundry-batch', daemon=True)
        self.worker.start()

    def pause(self):
        """Laufenden Remote-Job beenden lassen; zwischen Jobs anhalten."""
        self.stop.set()
        self.notify('info', 'Pause angefordert. Laufender ComfyUI-Auftrag wird fertig abgeholt.')

    def reset(self, selected_rels: list[str]):
        if self.running:
            raise RuntimeError('Zurücksetzen nicht während eines laufenden Batch.')
        for rel in selected_rels:
            job = self.store.jobs.get(rel)
            if job:
                job.history.append({'status': job.status, 'prompt_id': job.prompt_id,
                                    'message': job.message, 'finished': time.time()})
                job.status = 'bereit'
                job.prompt_id = ''
                job.fingerprint = ''
                job.message = ''
                job.replace = False
        self.store.save()
        self.notify('refresh', 'Zurückgesetzte Jobs können erneut gestartet werden.')

    def _update(self, job: Job, status: str, message: str = ''):
        job.status = status
        job.message = message
        self.store.put(job)
        self.notify('job', job.rel + '  ·  ' + status + (('  ·  ' + message) if message else ''))

    def _run(self, rels: list[str], replace: bool):
        try:
            with FolderLock(self.root):
                self._process(rels, replace)
        except Exception as exc:
            self.notify('error', f'Batch abgebrochen: {exc}')
        finally:
            self.notify('finished', 'Batch beendet oder pausiert.')

    def _process(self, rels: list[str], replace: bool):
        client = self.client_factory(self.settings)
        workflow = Workflow(self.settings.workflow_path)
        image_node, output_node = workflow.resolve(self.settings.image_node,
                                                   self.settings.output_node,
                                                   self.settings.image_input,
                                                   self.settings.prefix_input)
        monitor = self.monitor_factory(self.settings.gpu_index)
        workflow_hash = fingerprint(workflow.path)
        output = OutputManager(self.root)
        # aktuelle Pfade erneut ermitteln, auch nach Umbenennungen.
        by_rel = {a.rel: a for a in scan(self.root)}
        processed = 0
        for rel in rels:
            if self.stop.is_set():
                break
            asset = by_rel.get(rel)
            if asset is None:
                self.notify('error', 'Asset nicht mehr vorhanden: ' + rel)
                continue
            if asset.status == 'gesperrt':
                self.notify('error', 'Asset gesperrt: ' + rel)
                continue
            if asset.target.exists() and not replace:
                self.notify('info', 'Vorhandenes GLB übersprungen: ' + rel)
                continue
            prev = self.store.jobs.get(rel)
            # Ein bereits gesendeter Auftrag darf nicht blind erneut gesendet werden.
            resume = bool(prev and prev.prompt_id and prev.status in
                          ('vorbereitet', 'eingereicht', 'läuft', 'unklar', 'zeitlimit', 'ausgabe-fehler'))
            try:
                validate_png(asset.image)
                digest = fingerprint(asset.image)
                if resume and digest != prev.fingerprint:
                    self._update(prev, 'bild-geändert', 'PNG seit Generierung verändert. Manuell zurücksetzen.')
                    self.notify('error', 'Bild geändert: ' + rel)
                    continue
                if prev and prev.status in ('fehler', 'bild-geändert'):
                    self.notify('info', 'Fehlerjob erst explizit zurücksetzen: ' + rel)
                    continue
                if prev and prev.status == 'unklar' and not prev.prompt_id:
                    self.notify('error', 'Unklarer Job ohne ID, manuell zurücksetzen: ' + rel)
                    break
                if processed:
                    if not cooldown(self.settings.cooldown_seconds, self.stop,
                                    lambda msg: self.notify('info', msg)):
                        break
                if not resume:
                    try:
                        available = wait_for_resources(monitor, self.settings, self.stop,
                                                       lambda msg: self.notify('info', msg))
                    except TimeoutError as exc:
                        self.notify('error', str(exc))
                        break
                    if not available:
                        break
                    job = Job(rel=rel, fingerprint=digest, replace=replace,
                              output_node=output_node, workflow_hash=workflow_hash,
                              status='bereit', started=time.time())
                    self._update(job, 'hochladen', 'Bild wird an lokales ComfyUI übertragen')
                    # Upload ist idempotent zum Asset (UUID im Dateinamen); prompt muss erst danach erstellt werden.
                    prompt_id = str(uuid.uuid4())
                    upload_name = client.upload_png(asset.image, prompt_id)
                    prompt = workflow.build(upload_name, prompt_id, image_node, output_node,
                                            self.settings.image_input, self.settings.prefix_input)
                    job.prompt_id = prompt_id
                    self._update(job, 'vorbereitet', 'Prompt-ID vor dem POST gesichert')
                    # Mögliches POST-Timeout: Auftrag kann bereits in ComfyUI existieren.
                    client.submit(prompt, prompt_id)
                    self._update(job, 'eingereicht', 'ComfyUI-Auftrag bestätigt')
                else:
                    job = prev
                    self.notify('info', 'Vorhandenen ComfyUI-Auftrag fortsetzen: ' + job.prompt_id)
                    if job.workflow_hash and job.workflow_hash != workflow_hash:
                        self.notify('info', 'Workflow seit Einreichung verändert; nutze den damals hinterlegten Output-Node.')
                self._update(job, 'läuft', 'Warte auf ComfyUI-Generierung')
                result = self._wait(client, job)
                if result is None:
                    break
                info = client.find_glb(result, job.output_node or output_node)
                # Keine stillschweigende Zuordnung, falls Bild sich zwischenzeitlich geändert hat.
                if fingerprint(asset.image) != job.fingerprint:
                    self._update(job, 'bild-geändert', 'PNG während Generierung geändert. GLB nicht veröffentlicht.')
                    continue
                with client.download(info) as data:
                    output.publish(asset.target, data, replace=job.replace,
                                   backup=self.settings.backup_on_replace)
                job.finished = time.time()
                self._update(job, 'fertig', str(asset.target.relative_to(self.root)))
                processed += 1
            except UncertainSubmission as exc:
                # Existenz per History/Queue bei Neustart oder via manuelle Wiederaufnahme prüfen.
                job = self.store.jobs.get(rel)
                if job:
                    self._update(job, 'unklar', str(exc))
                self.notify('error', 'Einreichung unklar; keine automatische Wiederholung: ' + rel)
                break
            except FileExistsError as exc:
                job = self.store.jobs.get(rel)
                if job:
                    self._update(job, 'ausgabe-fehler', str(exc))
                self.notify('error', f'Dateikonflikt bei {rel}: {exc}')
            except Exception as exc:
                job = self.store.jobs.get(rel)
                if job:
                    # Wenn ComfyUI schon einen Auftrag hat, bleibt seine ID für die Wiederaufnahme erhalten.
                    kind = 'ausgabe-fehler' if job.prompt_id and job.status == 'läuft' else 'fehler'
                    self._update(job, kind, str(exc)[:1000])
                self.notify('error', f'{rel}: {exc}')
                # Wenn schon ein Remote-Job läuft, sicherheitshalber keine weiteren schicken.
                if job and job.prompt_id:
                    break

    def _wait(self, client, job: Job) -> dict | None:
        until = time.monotonic() + self.settings.max_job_seconds
        unknown_since = None
        while True:
            try:
                status, item = client.lookup(job.prompt_id)
            except ComfyError as exc:
                self.notify('info', f'ComfyUI-Abfrage fehlgeschlagen; versuche erneut: {exc}')
                status, item = 'running', None
            if status == 'done':
                return item
            if status == 'error':
                detail = str(item.get('status', {}).get('messages', ''))[:350]
                self._update(job, 'fehler', 'ComfyUI-Fehler: ' + detail)
                return None
            if status == 'unknown':
                if unknown_since is None:
                    unknown_since = time.monotonic()
                    self.notify('info', 'Auftrag noch nicht in History/Queue; kurzer Nachlauf vor Unklar-Status.')
                elif time.monotonic() - unknown_since >= max(5, self.settings.poll_seconds * 2):
                    self._update(job, 'unklar', 'Auftrag weder in History noch in Queue: keine Neugenerierung.')
                    return None
            else:
                unknown_since = None
            if time.monotonic() > until:
                self._update(job, 'zeitlimit', 'Auftrag läuft möglicherweise weiter; erneut verbinden statt neu senden.')
                return None
            # Pause ist soft; Remote-Job wird fertig abgeholt.
            time.sleep(self.settings.poll_seconds)
