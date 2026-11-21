"""Loopback-only HTTP application with token-protected JSON API and static assets.

No external JS, hosted service, backend framework, or internet connection required.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser

from .dashboard import DashboardController

FRONTEND = Path(__file__).resolve().parent / 'frontend'


class DashboardServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True
    def __init__(self, controller=None, port=0):
        self.controller = controller or DashboardController()
        self.auth_token = secrets.token_urlsafe(32)
        super().__init__(('127.0.0.1', port), DashboardHandler)
    @property
    def url(self):
        return f'http://127.0.0.1:{self.server_port}/?token={self.auth_token}'


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer
    protocol_version = 'HTTP/1.1'
    def log_message(self, fmt, *args):
        # Loopback logs can contain URL tokens: never echo them.
        return

    def _headers(self, content_type, length, code=200, cache=False, download=None):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(length))
        self.send_header('X-Content-Type-Options', 'nosniff')
        if download:
            self.send_header('Content-Disposition', f'attachment; filename="{download}"')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'public, max-age=3600' if cache else 'no-store')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('X-Frame-Options', 'DENY')
        if content_type.startswith('text/html'):
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.end_headers()

    def _send(self, body, mime='application/json; charset=utf-8', code=200, cache=False, download=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self._headers(mime, len(body), code, cache, download=download)
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _fail(self, msg, code=400):
        self._send({'ok': False, 'error': str(msg)[:900]}, code=code)

    def _authorized(self):
        provided = self.headers.get('X-AssetFoundry-Token', '')
        return secrets.compare_digest(provided, self.server.auth_token)

    def _same_origin(self):
        origin = self.headers.get('Origin', '')
        return (not origin or origin == f'http://127.0.0.1:{self.server.server_port}')

    def do_GET(self):
        parts = urlsplit(self.path)
        p = parts.path
        if p == '/' or p == '/index.html':
            query = parse_qs(parts.query)
            if query.get('token', [''])[0] != self.server.auth_token:
                return self._fail('Nicht autorisiert.', 403)
            return self._static('index.html')
        static_files = {'/static/app.js': 'app.js', '/static/i18n.js': 'i18n.js',
                        '/static/styles.css': 'styles.css',
                        '/static/locales/de.json': 'locales/de.json',
                        '/static/locales/en.json': 'locales/en.json'}
        if p in static_files:
            return self._static(static_files[p])
        if not p.startswith('/api/'):
            return self._fail('Nicht gefunden.', 404)
        if not self._authorized() or not self._same_origin():
            return self._fail('Nicht autorisiert.', 403)
        q = parse_qs(parts.query)
        c = self.server.controller
        try:
            if p == '/api/state':
                return self._send(c.snapshot(q.get('q', [''])[0], q.get('filter', ['all'])[0],
                                             q.get('page', ['0'])[0], q.get('size', ['100'])[0]))
            if p == '/api/workflow':
                path = q.get('path', [''])[0] or c.settings.workflow_path
                return self._send({'ok': True, **c.inspect_workflow(path)})
            if p == '/api/thumbnail':
                body, mime = c.thumbnail(q.get('rel', [''])[0])
                return self._send(body, mime)
            if p == '/api/preview':
                body, mime = c.preview(q.get('rel', [''])[0])
                return self._send(body, mime)
            return self._fail('Nicht gefunden.', 404)
        except FileNotFoundError as exc:
            return self._fail(exc, 404)
        except (ValueError, RuntimeError, OSError, TypeError) as exc:
            return self._fail(exc)

    def _static(self, name):
        mime = {'index.html': 'text/html; charset=utf-8', 'app.js': 'text/javascript; charset=utf-8',
                'i18n.js': 'text/javascript; charset=utf-8',
                'styles.css': 'text/css; charset=utf-8',
                'locales/de.json': 'application/json; charset=utf-8',
                'locales/en.json': 'application/json; charset=utf-8'}
        try:
            data = (FRONTEND / name).read_bytes()
            return self._send(data, mime[name], cache=name != 'index.html')
        except OSError:
            return self._fail('Frontend-Datei fehlt.', 500)

    def do_POST(self):
        p = urlsplit(self.path).path
        if not p.startswith('/api/'):
            return self._fail('Nicht gefunden.', 404)
        if not self._authorized() or not self._same_origin():
            return self._fail('Nicht autorisiert.', 403)
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            return self._fail('Nur JSON-Anfragen erlaubt.', 415)
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 64 * 1024:
                return self._fail('Anfrage zu gross oder leer.', 413)
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError('JSON-Objekt erwartet.')
            c = self.server.controller
            if p == '/api/scan':
                result = c.start_scan(str(data.get('root', '')).strip())
            elif p == '/api/choose-folder':
                result = {'path': self._choose_file(True, c.settings.root)}
            elif p == '/api/choose-workflow':
                result = {'path': self._choose_file(False, c.settings.workflow_path)}
            elif p == '/api/toggle':
                if not isinstance(data.get('selected'), bool): raise ValueError('selected muss Boolean sein.')
                result = c.toggle(str(data.get('rel', '')), data['selected'])
            elif p == '/api/select':
                result = c.select(data.get('mode'), data.get('q', ''), data.get('filter', 'all'))
            elif p == '/api/settings':
                result = c.update_settings(data)
            elif p == '/api/language':
                result = c.update_language(data.get('language'))
            elif p == '/api/workflow':
                result = c.save_workflow(data)
            elif p == '/api/ping':
                result = c.test_connection()
            elif p == '/api/start':
                result = c.start_batch(data.get('replace', False))
            elif p == '/api/pause':
                result = c.pause()
            elif p == '/api/reset':
                result = c.reset_selected()
            elif p == '/api/diagnostics':
                if not isinstance(data.get('include_paths', False), bool):
                    raise ValueError('Ungültige Diagnose-Option.')
                body = c.diagnostics(include_paths=data.get('include_paths', False))
                return self._send(body, 'application/zip', download='AssetFoundry-Diagnose-' + time.strftime('%Y%m%d-%H%M%S') + '.zip')
            elif p == '/api/quit':
                if c.running:
                    raise RuntimeError('Laufender Batch: zuerst pausieren und abschliessen lassen.')
                c.close()
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                result = {'ok': True}
            else:
                return self._fail('Nicht gefunden.', 404)
            return self._send({'ok': True, **result})
        except (ValueError, RuntimeError, TypeError, OSError, KeyError) as exc:
            return self._fail(exc)

    def _choose_file(self, directory, current):
        # A native Windows chooser is optional convenience; manual paths always work.
        try:
            import tkinter as tk
            from tkinter import filedialog
        except ImportError as exc:
            raise RuntimeError('Kein nativer Dateidialog verfügbar. Pfad direkt eingeben.') from exc
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes('-topmost', True)
            if directory:
                return filedialog.askdirectory(parent=root, initialdir=current if Path(current).is_dir() else str(Path.home()), title='Choose asset folder' if getattr(self.server.controller.settings, 'language', 'de') == 'en' else 'Asset-Ordner wählen') or ''
            return filedialog.askopenfilename(parent=root, initialdir=str(Path(current).parent) if current else str(Path.cwd()),
                                              filetypes=[('ComfyUI-API-Workflow', '*.json')], title='Choose ComfyUI API JSON' if getattr(self.server.controller.settings, 'language', 'de') == 'en' else 'ComfyUI API-JSON wählen') or ''
        finally:
            root.destroy()


def _launch_window(url):
    """Prefer Edge/Chrome app window, fall back to the default browser."""
    if os.name == 'nt':
        common = [shutil.which('msedge'), shutil.which('chrome'),
                  os.path.join(os.environ.get('PROGRAMFILES(X86)', ''), 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
                  os.path.join(os.environ.get('PROGRAMFILES', ''), 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
                  os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'Edge', 'Application', 'msedge.exe')]
        for browser in common:
            if browser and Path(browser).is_file():
                try:
                    subprocess.Popen([browser, '--app=' + url, '--window-size=1460,930'],
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return 'app'
                except OSError:
                    continue
    webbrowser.open(url)
    return 'browser'


def run(open_browser=True, port=0):
    server = DashboardServer(port=port)
    print('3D Asset Generator Batch – lokale Oberfläche: ' + f'http://127.0.0.1:{server.server_port}/')
    print('Zum Beenden in der Oberfläche „Beenden“ wählen oder hier Strg+C drücken.')
    if open_browser:
        _launch_window(server.url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.controller.close()
        server.server_close()


if __name__ == '__main__':
    run()
