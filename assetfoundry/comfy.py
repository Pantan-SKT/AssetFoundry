"""HTTP-Schnittstelle der lokalen ComfyUI-Instanz (nur 127.0.0.1/localhost)."""
from __future__ import annotations
import contextlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from .models import Settings


class ComfyError(RuntimeError):
    pass


class UncertainSubmission(ComfyError):
    """POST /prompt kann erfolgreich gewesen sein; nicht automatisch erneut senden."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ComfyError('HTTP-Weiterleitung abgelehnt.')


class ComfyClient:
    def __init__(self, settings: Settings, timeout=30):
        settings.validate()
        self.url = settings.comfy_url.rstrip('/')
        self.timeout = timeout
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))

    def _request(self, path, data=None, headers=None, method=None, timeout=None):
        req = urllib.request.Request(self.url + path, data=data,
                                     headers=headers or {}, method=method)
        try:
            return self.opener.open(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as exc:
            body = exc.read(4096).decode('utf-8', 'replace')
            raise ComfyError(f'ComfyUI HTTP {exc.code}: {body[:700]}') from None
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise ComfyError('ComfyUI nicht erreichbar: ' + str(exc)[:250]) from None

    def _json(self, path, obj=None):
        data = None if obj is None else json.dumps(obj).encode('utf-8')
        with self._request(path, data=data,
                           headers={'Content-Type': 'application/json'} if data else {}) as resp:
            result = json.load(resp)
        return result

    def ping(self):
        return self._json('/system_stats')

    def upload_png(self, path: Path, identifier: str) -> str:
        """Multipart-Bildtransfer ohne externe Pakete; eindeutiger Dateiname pro Auftrag."""
        path = Path(path)
        if path.stat().st_size > 100 * 1024 * 1024:
            raise ComfyError('PNG zu gross (maximal 100 MiB).')
        boundary = '----assetfoundry' + uuid.uuid4().hex
        filename = 'af_' + identifier.replace('-', '') + '.png'
        header = (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
                  f'filename="{filename}"\r\nContent-Type: image/png\r\n\r\n').encode('ascii')
        footer = (f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="type"\r\n\r\n'
                  f'input\r\n--{boundary}--\r\n').encode('ascii')
        # bounded to 100 MiB; prevents complexity of streaming urllib multipart.
        body = header + path.read_bytes() + footer
        with self._request('/upload/image', data=body,
                           headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
                           timeout=max(30, len(body) // (1024 * 1024) * 5)) as resp:
            obj = json.load(resp)
        if not isinstance(obj, dict) or not isinstance(obj.get('name'), str):
            raise ComfyError('ComfyUI meldet keinen gültigen PNG-Upload.')
        if obj.get('subfolder'):
            raise ComfyError('Unerwarteter Upload-Unterordner. Keine unklare Datei-Zuordnung.')
        return obj['name']

    def submit(self, prompt: dict, prompt_id: str):
        """Kein Blind-Retry! Die prompt_id ist vor diesem Aufruf persistiert."""
        data = json.dumps({'prompt': prompt, 'client_id': 'assetfoundry-' + str(uuid.uuid4()),
                           'prompt_id': prompt_id}).encode('utf-8')
        try:
            with self._request('/prompt', data=data, headers={'Content-Type': 'application/json'},
                               timeout=60) as resp:
                answer = json.load(resp)
        except ComfyError as exc:
            # Definitiv ablehnende HTTP-Fehler enthalten /prompt validation errors.
            if str(exc).startswith('ComfyUI HTTP 4'):
                raise
            raise UncertainSubmission(str(exc)) from None
        except (ValueError, OSError) as exc:
            raise UncertainSubmission('Auftragsantwort unklar: ' + str(exc)) from None
        if not isinstance(answer, dict) or not answer.get('prompt_id'):
            raise UncertainSubmission('ComfyUI-Antwort ohne prompt_id; bitte Auftrag prüfen.')
        if str(answer['prompt_id']) != prompt_id:
            raise UncertainSubmission('ComfyUI bestätigte eine andere Auftrags-ID.')
        return answer

    def history(self, prompt_id: str) -> dict | None:
        response = self._json('/history/' + urllib.parse.quote(prompt_id, safe=''))
        if not isinstance(response, dict):
            raise ComfyError('Ungültige History-Antwort.')
        item = response.get(prompt_id)
        return item if isinstance(item, dict) else None

    def is_in_queue(self, prompt_id: str) -> bool:
        obj = self._json('/queue')
        if not isinstance(obj, dict):
            return False
        for key in ('queue_running', 'queue_pending'):
            if any(isinstance(item, list) and len(item) > 1 and item[1] == prompt_id
                   for item in obj.get(key, [])):
                return True
        return False

    def lookup(self, prompt_id: str) -> tuple[str, dict | None]:
        item = self.history(prompt_id)
        if item:
            status = item.get('status', {})
            if status.get('status_str') == 'error' or status.get('completed') is False:
                return 'error', item
            # Some ComfyUI versions do not report completed explicitly.
            if status.get('completed') is True or status.get('status_str') == 'success' or item.get('outputs'):
                return 'done', item
            return 'pending', item
        return ('running', None) if self.is_in_queue(prompt_id) else ('unknown', None)

    @staticmethod
    def find_glb(item: dict, output_node: str) -> dict:
        outputs = item.get('outputs')
        node = (outputs or {}).get(str(output_node)) if isinstance(outputs, dict) else None
        if not isinstance(node, dict):
            raise ComfyError('GLB-Export-Node hat keine Ausgabedaten in ComfyUI-History. '
                             'Bitte SaveGLB oder kompatiblen Save-Node im API-Workflow verwenden.')
        models = []
        # Native SaveGLB uses "3d"; custom exporters may use "files" or "models".
        for key in ('3d', 'files', 'models', 'meshes'):
            entries = node.get(key, [])
            if isinstance(entries, dict):
                entries = [entries]
            if not isinstance(entries, list):
                continue
            for x in entries:
                if isinstance(x, dict) and str(x.get('filename', '')).lower().endswith('.glb'):
                    models.append(x)
        if len(models) != 1:
            raise ComfyError(f'Export-Node liefert {len(models)} GLB-Dateien; genau eine wird erwartet. '
                             'Output-Node im Workflow-Tab prüfen.')
        info = models[0]
        filename = info.get('filename', '')
        subfolder = info.get('subfolder', '') or ''
        if (not isinstance(filename, str) or not filename.lower().endswith('.glb') or
                '/' in filename or '\\' in filename or filename in ('.', '..') or
                not isinstance(subfolder, str) or subfolder.startswith(('/', '\\')) or
                '..' in subfolder.replace('\\', '/').split('/') or
                info.get('type', 'output') != 'output'):
            raise ComfyError('Unsichere oder unerwartete GLB-Referenz in ComfyUI-History.')
        return {'filename': filename, 'subfolder': subfolder, 'type': 'output'}

    @contextlib.contextmanager
    def download(self, info: dict):
        query = urllib.parse.urlencode(info)
        with self._request('/view?' + query, timeout=120) as response:
            yield response
