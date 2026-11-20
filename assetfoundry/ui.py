"""Windows-Desktop-Oberfläche (Tkinter), strikt getrennt von der Batch-Engine."""
from __future__ import annotations
import logging
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .assets import scan
from .batch import BatchRunner
from .comfy import ComfyClient
from .models import Settings, load_settings, save_settings, settings_file
from .resources import ResourceMonitor
from .state import JobStore
from .workflow import Workflow

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None

BASE = Path(__file__).resolve().parent.parent


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f'AssetFoundry {__version__}  ·  Local 3D Asset Pipeline')
        self.geometry('1220x800')
        self.minsize(900, 620)
        self.events = queue.Queue()
        self.assets = []
        self.selected = set()
        self.runner = None
        self.store = None
        self.scanning = False
        self._photo = None
        self._replace_mode = False
        self._run_started_at = 0.0
        self._monitor = None
        self._closed = threading.Event()
        try:
            self.settings = load_settings()
        except Exception as exc:
            messagebox.showwarning('Einstellungen', f'Einstellungen konnten nicht geladen werden: {exc}\n'
                                   'Es werden sichere Standardwerte verwendet.')
            self.settings = Settings()
        if not self.settings.workflow_path or not Path(self.settings.workflow_path).is_file():
            for name in ('API.json', 'api.json', 'workflow_api.json'):
                found = BASE / 'workflows' / name
                if found.exists():
                    self.settings.workflow_path = str(found)
                    break
        self._style()
        self._build()
        self.protocol('WM_DELETE_WINDOW', self._close)
        self.after(160, self._drain)
        threading.Thread(target=self._metrics_loop, daemon=True).start()
        if self.settings.root and Path(self.settings.root).is_dir():
            self.after(350, self.scan_folder)
        if self.settings.workflow_path and Path(self.settings.workflow_path).is_file():
            self.after(400, self.inspect_workflow)

    def _style(self):
        self.configure(bg='#141922')
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', background='#141922', foreground='#e6edf5', font=('Segoe UI', 10))
        style.configure('TFrame', background='#141922')
        style.configure('TLabel', background='#141922', foreground='#e6edf5')
        style.configure('Hint.TLabel', foreground='#97a7ba')
        style.configure('Title.TLabel', font=('Segoe UI Semibold', 21), foreground='#ffffff')
        style.configure('TButton', background='#283344', foreground='#e6edf5', padding=(12, 8), borderwidth=0)
        style.map('TButton', background=[('active', '#36475d')], foreground=[('disabled', '#788496')])
        style.configure('Accent.TButton', background='#0caa86', foreground='#ffffff', padding=(13, 9))
        style.map('Accent.TButton', background=[('active', '#0f9679')])
        style.configure('TEntry', fieldbackground='#212b38', foreground='white', insertcolor='white', padding=6)
        style.configure('TCombobox', fieldbackground='#212b38', foreground='white', padding=5)
        style.configure('TNotebook', background='#141922', borderwidth=0)
        style.configure('TNotebook.Tab', background='#222b39', foreground='#b5c2d1', padding=(16, 10))
        style.map('TNotebook.Tab', background=[('selected', '#34445a')], foreground=[('selected', 'white')])
        style.configure('Treeview', background='#1b2330', foreground='#e7edf5', fieldbackground='#1b2330',
                        rowheight=31, borderwidth=0)
        style.configure('Treeview.Heading', background='#303c4b', foreground='#f0f4f8', padding=(7, 9))
        style.map('Treeview', background=[('selected', '#245b72')])
        style.configure('Horizontal.TProgressbar', background='#12ba90', troughcolor='#2b3544', thickness=10)
        style.configure('Horizontal.TScale', background='#141922')

    def _build(self):
        header = ttk.Frame(self)
        header.pack(fill='x', padx=24, pady=(20, 7))
        ttk.Label(header, text='AssetFoundry', style='Title.TLabel').pack(side='left')
        ttk.Label(header, text='  LOCAL 3D ASSET PIPELINE  ·  v' + __version__,
                  style='Hint.TLabel').pack(side='left', padx=14, pady=(10, 0))
        self.connection = tk.StringVar(value='ComfyUI: nicht geprüft')
        ttk.Label(header, textvariable=self.connection, style='Hint.TLabel').pack(side='right', pady=(10, 0))

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill='both', expand=True, padx=20, pady=(4, 8))
        self.assets_tab = ttk.Frame(self.notebook)
        self.workflow_tab = ttk.Frame(self.notebook)
        self.settings_tab = ttk.Frame(self.notebook)
        self.log_tab = ttk.Frame(self.notebook)
        for t, label in ((self.assets_tab, 'Assets & Batch'), (self.workflow_tab, 'Workflow'),
                         (self.settings_tab, 'Leistung & Einstellungen'), (self.log_tab, 'Protokoll')):
            self.notebook.add(t, text=label)
        self._assets_ui()
        self._workflow_ui()
        self._settings_ui()
        self._log_ui()
        footer = ttk.Frame(self)
        footer.pack(fill='x', padx=25, pady=(0, 15))
        self.status_line = tk.StringVar(value='Bereit. PNG-Ordner auswählen und API-Workflow laden.')
        ttk.Label(footer, textvariable=self.status_line, style='Hint.TLabel').pack(side='left')
        self.progress = ttk.Progressbar(footer, length=180, maximum=100)
        self.progress.pack(side='right')

    def _assets_ui(self):
        parent = self.assets_tab
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=12, padx=12)
        self.root_var = tk.StringVar(value=self.settings.root)
        ttk.Label(row, text='Asset-Ordner').pack(side='left', padx=(0, 10))
        ttk.Entry(row, textvariable=self.root_var).pack(side='left', expand=True, fill='x')
        ttk.Button(row, text='Wählen…', command=self.choose_folder).pack(side='left', padx=(9, 4))
        ttk.Button(row, text='Scannen', command=self.scan_folder).pack(side='left')

        stats = ttk.Frame(parent)
        stats.pack(fill='x', padx=14, pady=(1, 12))
        self.stats_var = tk.StringVar(value='0 PNG  ·  0 vorhanden  ·  0 fehlen  ·  0 ausgewählt')
        ttk.Label(stats, textvariable=self.stats_var).pack(side='left')
        ttk.Label(stats, text='Doppelklick oder Leertaste: Auswahl umschalten',
                  style='Hint.TLabel').pack(side='right')
        action = ttk.Frame(parent)
        action.pack(fill='x', padx=12, pady=(0, 9))
        self.filter_var = tk.StringVar(value='Alle')
        self.filterbox = ttk.Combobox(action, textvariable=self.filter_var, state='readonly',
                                      width=16, values=['Alle', 'Fehlende GLB', 'Vorhandene GLB',
                                                        'Fehler', 'Abgeschlossen'])
        self.filterbox.pack(side='left')
        self.filterbox.bind('<<ComboboxSelected>>', lambda _: self._refresh_table())
        self.search_var = tk.StringVar()
        self.search_var.trace_add('write', lambda *_: self._refresh_table())
        ttk.Entry(action, textvariable=self.search_var, width=30).pack(side='left', padx=7)
        ttk.Button(action, text='Fehlende wählen', command=self.select_missing).pack(side='left', padx=3)
        ttk.Button(action, text='Sichtbare wählen', command=lambda: self.select_visible(True)).pack(side='left', padx=3)
        ttk.Button(action, text='Auswahl löschen', command=self.select_none).pack(side='left', padx=3)

        mid = ttk.Frame(parent)
        mid.pack(fill='both', expand=True, padx=12)
        tablebox = ttk.Frame(mid)
        tablebox.pack(side='left', fill='both', expand=True)
        cols = ('choose', 'file', 'state', 'note')
        self.tree = ttk.Treeview(tablebox, columns=cols, show='headings', selectmode='extended')
        for key, text, width, anchor in (
                ('choose', '✓', 45, 'center'), ('file', 'Asset / Unterordner', 430, 'w'),
                ('state', 'Status', 132, 'w'), ('note', 'Hinweis', 245, 'w')):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, minwidth=35, anchor=anchor, stretch=key in ('file', 'note'))
        self.tree.tag_configure('done', foreground='#83dbc5')
        self.tree.tag_configure('failed', foreground='#ffb1ac')
        self.tree.tag_configure('old', foreground='#b1bccb')
        self.tree.pack(side='left', fill='both', expand=True)
        sb = ttk.Scrollbar(tablebox, command=self.tree.yview)
        sb.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind('<Double-1>', self._toggle_row)
        self.tree.bind('<space>', self._toggle_keyboard)
        self.tree.bind('<<TreeviewSelect>>', self._preview_selected)

        preview = ttk.Frame(mid, width=215)
        preview.pack(side='right', fill='y', padx=(12, 0))
        preview.pack_propagate(False)
        ttk.Label(preview, text='Bildvorschau').pack(anchor='w', pady=(3, 12))
        self.preview_label = tk.Label(preview, bg='#222c3b', fg='#b4c7d7', width=28,
                                      height=13, text='PNG auswählen', compound='center')
        self.preview_label.pack(fill='x')
        self.preview_text = tk.StringVar(value='')
        ttk.Label(preview, textvariable=self.preview_text, style='Hint.TLabel',
                  wraplength=210).pack(fill='x', pady=12)

        controls = ttk.Frame(parent)
        controls.pack(fill='x', padx=12, pady=14)
        self.start_btn = ttk.Button(controls, text='▶ Ausgewählte generieren / fortsetzen',
                                    style='Accent.TButton', command=self.start_batch)
        self.start_btn.pack(side='left')
        ttk.Button(controls, text='II Pause nach aktuellem Auftrag', command=self.pause).pack(side='left', padx=8)
        ttk.Button(controls, text='Fehler/Job zurücksetzen…', command=self.reset_jobs).pack(side='left')

    def _workflow_ui(self):
        tab = self.workflow_tab
        t = ttk.Frame(tab); t.pack(fill='x', padx=25, pady=24)
        ttk.Label(t, text='ComfyUI-Workflow (API-JSON)', font=('Segoe UI Semibold', 15)).pack(anchor='w')
        ttk.Label(t, text='Exportiere in ComfyUI über „Save (API Format)“. Lege die Datei in workflows/API.json ab '
                  'oder wähle sie hier. Keine Änderungen am JSON nötig.',
                  style='Hint.TLabel', wraplength=850).pack(anchor='w', pady=(6, 16))
        self.workflow_var = tk.StringVar(value=self.settings.workflow_path)
        line = ttk.Frame(t); line.pack(fill='x')
        ttk.Entry(line, textvariable=self.workflow_var).pack(side='left', fill='x', expand=True)
        ttk.Button(line, text='JSON wählen…', command=self.choose_workflow).pack(side='left', padx=8)
        ttk.Button(line, text='Analysieren', command=self.inspect_workflow).pack(side='left')
        self.workflow_info = tk.StringVar(value='Noch kein Workflow analysiert.')
        ttk.Label(t, textvariable=self.workflow_info, style='Hint.TLabel', wraplength=950).pack(anchor='w', pady=16)
        ttk.Label(t, text='Bild-Eingangs-Node').pack(anchor='w')
        self.image_node_var = tk.StringVar()
        self.img_options = {}
        self.image_combo = ttk.Combobox(t, textvariable=self.image_node_var, width=70)
        self.image_combo.pack(anchor='w', pady=(4, 12))
        ttk.Label(t, text='GLB-Speicher-Node').pack(anchor='w')
        self.output_node_var = tk.StringVar()
        self.out_options = {}
        self.output_combo = ttk.Combobox(t, textvariable=self.output_node_var, width=70)
        self.output_combo.pack(anchor='w', pady=(4, 12))
        line2 = ttk.Frame(t); line2.pack(anchor='w', pady=5)
        ttk.Label(line2, text='Bild-Input-Feld:').pack(side='left')
        self.image_input_var = tk.StringVar(value=self.settings.image_input)
        ttk.Entry(line2, textvariable=self.image_input_var, width=17).pack(side='left', padx=(8, 20))
        ttk.Label(line2, text='Output-Prefix-Feld:').pack(side='left')
        self.prefix_input_var = tk.StringVar(value=self.settings.prefix_input)
        ttk.Entry(line2, textvariable=self.prefix_input_var, width=22).pack(side='left', padx=8)
        ttk.Button(t, text='Workflow-Zuordnung speichern', style='Accent.TButton',
                   command=self.save_workflow).pack(anchor='w', pady=14)
        ttk.Label(t, text='Der Export-Node muss in der ComfyUI-History genau ein GLB melden. '
                  'Standardmässig unterstützt: SaveGLB, Save3DAdvanced und kompatible Nodes mit „3d“-Dateiausgabe.',
                  style='Hint.TLabel', wraplength=900).pack(anchor='w', pady=8)

    def _settings_ui(self):
        tab = self.settings_tab
        container = ttk.Frame(tab); container.pack(fill='both', expand=True, padx=24, pady=18)
        ttk.Label(container, text='Ressourcen beobachten & Generierung dosieren',
                  font=('Segoe UI Semibold', 15)).pack(anchor='w')
        ttk.Label(container, text='Die Lastgrenzen drosseln nur die Einreichung neuer Aufträge. '
                  'ComfyUI selbst verwaltet GPU/CPU/VRAM und wird durch diese App nicht künstlich gedeckelt.',
                  style='Hint.TLabel', wraplength=950).pack(anchor='w', pady=(6, 14))
        metrics = ttk.Frame(container); metrics.pack(fill='x', pady=6)
        self.metric_vars = {}
        self.metric_bars = {}
        for i, key in enumerate(('CPU', 'RAM', 'GPU', 'VRAM')):
            cell = ttk.Frame(metrics); cell.grid(row=0, column=i, sticky='ew', padx=(0, 13))
            metrics.columnconfigure(i, weight=1)
            var = tk.StringVar(value=f'{key}: –')
            ttk.Label(cell, textvariable=var).pack(anchor='w')
            bar = ttk.Progressbar(cell, maximum=100)
            bar.pack(fill='x', pady=6)
            self.metric_vars[key] = var
            self.metric_bars[key] = bar
        self.gpu_label = tk.StringVar(value='GPU: Metriken werden geladen')
        ttk.Label(container, textvariable=self.gpu_label, style='Hint.TLabel').pack(anchor='w', pady=(0, 8))
        ttk.Separator(container).pack(fill='x', pady=4)
        urlrow = ttk.Frame(container); urlrow.pack(fill='x', pady=(6, 2))
        ttk.Label(urlrow, text='ComfyUI-Server (nur lokal)', width=43).pack(side='left')
        self.svars = {}
        self.svars['comfy_url'] = tk.StringVar(value=self.settings.comfy_url)
        ttk.Entry(urlrow, textvariable=self.svars['comfy_url'], width=40).pack(side='left', padx=(0, 12))
        body = ttk.Frame(container); body.pack(fill='x', pady=(5, 12))
        specs = [
            ('cooldown_seconds', 'Cooldown zwischen Jobs (Sekunden)', str(self.settings.cooldown_seconds)),
            ('cpu_limit', 'CPU-Grenze vor neuem Job (%)', str(self.settings.cpu_limit)),
            ('ram_limit', 'RAM-Grenze vor neuem Job (%)', str(self.settings.ram_limit)),
            ('gpu_limit', 'GPU-Auslastungsgrenze vor neuem Job (%)', str(self.settings.gpu_limit)),
            ('vram_limit', 'VRAM-Auslastungsgrenze vor neuem Job (%)', str(self.settings.vram_limit)),
            ('gpu_index', 'GPU für Monitoring (NVIDIA-Index)', str(self.settings.gpu_index)),
            ('max_resource_wait_seconds', 'Maximale Wartezeit auf freie Ressourcen (Sek.)',
             str(self.settings.max_resource_wait_seconds)),
            ('max_job_seconds', 'Maximale Wartezeit je Generierung (Sek.)', str(self.settings.max_job_seconds)),
            ('poll_seconds', 'ComfyUI-Statusabfrage alle (Sek.)', str(self.settings.poll_seconds)),
        ]
        for i, (key, label, val) in enumerate(specs):
            r, c = divmod(i, 2)
            box = ttk.Frame(body); box.grid(row=r, column=c, sticky='ew', padx=(0, 40), pady=5)
            body.columnconfigure(c, weight=1)
            ttk.Label(box, text=label, width=47).pack(side='left')
            var = tk.StringVar(value=val)
            self.svars[key] = var
            ttk.Entry(box, textvariable=var, width=15).pack(side='left', fill='x', expand=True)
        self.gate_var = tk.BooleanVar(value=self.settings.gate_resources)
        self.backup_var = tk.BooleanVar(value=self.settings.backup_on_replace)
        ttk.Checkbutton(container, text='Bei Lastgrenzen warten, bevor der nächste Job startet',
                        variable=self.gate_var).pack(anchor='w', pady=3)
        ttk.Checkbutton(container, text='Vor dem Überschreiben vorhandene GLBs sichern (.assetfoundry/backups)',
                        variable=self.backup_var).pack(anchor='w', pady=3)
        cmds = ttk.Frame(container); cmds.pack(fill='x', pady=7)
        ttk.Button(cmds, text='Einstellungen speichern', style='Accent.TButton',
                   command=self.save_config).pack(side='left')
        ttk.Button(cmds, text='ComfyUI-Verbindung testen', command=self.test_connection).pack(side='left', padx=10)
        ttk.Label(container, text='Hinweis: GPU-Index wählt die überwachte NVIDIA-GPU, NICHT die GPU eines '
                  'bereits laufenden ComfyUI-Prozesses. GPU-/VRAM-Limits sind Startbedingungen, '
                  'keine harten Leistungslimits. Unbekannte Messwerte blockieren nicht.',
                  style='Hint.TLabel', wraplength=1000).pack(anchor='w', pady=3)

    def _log_ui(self):
        frame = ttk.Frame(self.log_tab); frame.pack(fill='both', expand=True, padx=14, pady=14)
        self.logbox = tk.Text(frame, bg='#101722', fg='#c6d6e6', insertbackground='white',
                              font=('Consolas', 10), wrap='word', relief='flat', state='disabled')
        self.logbox.pack(side='left', fill='both', expand=True)
        sb = ttk.Scrollbar(frame, command=self.logbox.yview); sb.pack(side='right', fill='y')
        self.logbox.configure(yscrollcommand=sb.set)

    def _log(self, msg: str, error=False):
        text = '[' + time.strftime('%H:%M:%S') + '] ' + str(msg) + '\n'
        self.logbox.configure(state='normal')
        self.logbox.insert('end', text)
        if int(self.logbox.index('end-1c').split('.')[0]) > 5000:
            self.logbox.delete('1.0', '1000.0')
        self.logbox.see('end')
        self.logbox.configure(state='disabled')
        self.status_line.set(str(msg)[:160])
        try:
            p = settings_file().parent / 'activity.log'
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists() and p.stat().st_size > 3 * 1024 * 1024:
                p.replace(p.with_suffix('.previous.log'))
            with p.open('a', encoding='utf-8') as f:
                f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + str(msg)[:1200] + '\n')
        except OSError:
            pass

    def _drain(self):
        if self._closed.is_set():
            return
        n = 0
        while n < 200:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            n += 1
            if kind == 'scanned':
                self.scanning = False
                self.assets = data
                self.selected.intersection_update(a.rel for a in self.assets)
                if not self.selected:
                    self.selected = {a.rel for a in self.assets if a.status == 'bereit'}
                try:
                    self.store = JobStore(Path(self.settings.root))
                except Exception as exc:
                    self.store = None
                    self._log('Jobdatei beschädigt: ' + str(exc), True)
                self._refresh_table()
                self._log(f'Scan abgeschlossen: {len(self.assets)} PNG-Bilder in allen Unterordnern.')
            elif kind == 'metrics':
                for key, val in (('CPU', data.cpu), ('RAM', data.ram),
                                 ('GPU', data.gpu), ('VRAM', data.vram)):
                    self.metric_vars[key].set(f'{key}: {val:.0f} %' if val is not None else f'{key}: –')
                    self.metric_bars[key]['value'] = val or 0
                extra = (f'{data.vram_used_mib} / {data.vram_total_mib} MiB' if data.vram_total_mib else '')
                self.gpu_label.set((data.gpu_name + ' · ' + extra if data.gpu_name else data.warning))
            elif kind == 'connection':
                self.connection.set('ComfyUI: verbunden' if data[0] else 'ComfyUI: nicht erreichbar')
                self._log(data[1], not data[0])
            elif kind == 'job':
                self._log(data)
                self._refresh_table()
            elif kind == 'finished':
                self._log(data)
                self.after(500, lambda: self.scan_folder(silent=True))
            elif kind == 'error':
                self._log('FEHLER: ' + data, True)
            else:
                self._log(data)
        self.after(160, self._drain)

    def _metrics_loop(self):
        monitor = None
        current_index = None
        while not self._closed.is_set():
            try:
                if monitor is None or current_index != self.settings.gpu_index:
                    current_index = self.settings.gpu_index
                    monitor = ResourceMonitor(current_index)
                self.events.put(('metrics', monitor.sample()))
            except Exception as exc:
                self.events.put(('info', 'Monitoring: ' + str(exc)))
            self._closed.wait(2.5)

    def choose_folder(self):
        path = filedialog.askdirectory(title='Asset-Ordner mit PNG-Bildern wählen',
                                      initialdir=self.root_var.get() or str(Path.home()))
        if path:
            self.root_var.set(path)
            self.scan_folder()

    def scan_folder(self, silent=False):
        if self.runner and self.runner.running:
            if not silent:
                messagebox.showinfo('Batch aktiv', 'Ordner während der Generierung nicht neu scannen.')
            return
        if self.scanning:
            return
        root = Path(self.root_var.get().strip())
        if not root.is_dir():
            if not silent:
                messagebox.showerror('Ordner fehlt', 'Bitte einen existierenden Asset-Ordner wählen.')
            return
        self.scanning = True
        self.settings.root = str(root.resolve())
        self._log('Scanne PNGs und GLBs in ' + self.settings.root)
        def task():
            try:
                result = scan(root)
                self.events.put(('scanned', result))
            except Exception as exc:
                self.scanning = False
                self.events.put(('error', 'Scanner: ' + str(exc)))
        threading.Thread(target=task, daemon=True).start()
        try:
            save_settings(self.settings)
        except Exception:
            pass

    def _job_for(self, rel):
        active = bool(self.runner and self.runner.root == Path(self.settings.root).resolve())
        store = self.runner.store if active else self.store
        return store.jobs.get(rel) if store else None

    def _asset_status(self, asset):
        if asset.status in ('gesperrt', 'ungültige GLB'):
            return asset.status, asset.message
        if asset.target.exists():
            return 'vorhanden', 'GLB bereits am Zielort'
        job = self._job_for(asset.rel)
        if job and job.status not in ('fertig', 'bereit'):
            return job.status, job.message
        return 'bereit', 'Noch kein GLB'

    def _visible_assets(self):
        term = self.search_var.get().casefold().strip()
        result = []
        for a in self.assets:
            status, _ = self._asset_status(a)
            mode = self.filter_var.get()
            if term and term not in a.rel.casefold():
                continue
            if mode == 'Fehlende GLB' and a.target.exists():
                continue
            if mode == 'Vorhandene GLB' and not a.target.exists():
                continue
            if mode == 'Fehler' and status not in ('fehler', 'ungültige GLB', 'gesperrt',
                                                 'unklar', 'bild-geändert', 'ausgabe-fehler', 'zeitlimit'):
                continue
            if mode == 'Abgeschlossen' and status != 'vorhanden':
                continue
            result.append(a)
        return result

    def _refresh_table(self):
        # 3000 visible records at once; avoid freezing UI with huge libraries.
        visible = self._visible_assets()
        focus = self.tree.focus()
        self.tree.delete(*self.tree.get_children())
        for a in visible[:3000]:
            status, note = self._asset_status(a)
            tag = 'done' if status in ('fertig',) else ('old' if status == 'vorhanden' else
                  'failed' if status in ('fehler', 'ungültige GLB', 'gesperrt', 'unklar',
                                          'ausgabe-fehler', 'bild-geändert', 'zeitlimit') else '')
            self.tree.insert('', 'end', iid=a.rel, values=('☑' if a.rel in self.selected else '☐',
                              a.rel, status, note[:95]), tags=(tag,) if tag else ())
        if focus and self.tree.exists(focus):
            self.tree.focus(focus)
        present = sum(a.target.exists() for a in self.assets)
        self.stats_var.set(f'{len(self.assets)} PNG  ·  {present} GLB vorhanden  ·  '
                           f'{len(self.assets)-present} fehlen  ·  {len(self.selected)} ausgewählt'
                           + (f'  ·  {len(visible)-3000} weitere ausgeblendet' if len(visible)>3000 else ''))
        if self.runner and self.runner.running and self._replace_mode:
            finished = sum(1 for rel in self.selected if
                           (self.runner.store.jobs.get(rel) and
                            self.runner.store.jobs[rel].status == 'fertig' and
                            self.runner.store.jobs[rel].finished >= self._run_started_at))
        else:
            finished = sum(a.target.exists() for a in self.assets if a.rel in self.selected)
        self.progress['value'] = round(100 * finished / len(self.selected), 1) if self.selected else 0

    def _toggle_row(self, event):
        iid = self.tree.identify_row(event.y)
        if iid:
            self._toggle([iid])

    def _toggle_keyboard(self, event):
        ids = self.tree.selection() or ((self.tree.focus(),) if self.tree.focus() else ())
        self._toggle(ids)
        return 'break'

    def _toggle(self, rels):
        for rel in rels:
            if rel in self.selected:
                self.selected.remove(rel)
            else:
                a = next((x for x in self.assets if x.rel == rel), None)
                if a and a.status != 'gesperrt':
                    self.selected.add(rel)
        self._refresh_table()

    def select_missing(self):
        self.selected = {a.rel for a in self.assets if not a.target.exists()
                         and a.status != 'gesperrt'}
        self._refresh_table()

    def select_none(self):
        self.selected.clear()
        self._refresh_table()

    def select_visible(self, selected):
        for a in self._visible_assets():
            if selected and a.status != 'gesperrt':
                self.selected.add(a.rel)
            elif not selected:
                self.selected.discard(a.rel)
        self._refresh_table()

    def _preview_selected(self, *_):
        ids = self.tree.selection()
        if not ids:
            return
        a = next((x for x in self.assets if x.rel == ids[0]), None)
        if a is None:
            return
        self.preview_text.set(a.rel + ('\nGLB: ' + a.target.name if a.target.exists() else '\nGLB fehlt'))
        try:
            if Image:
                with Image.open(a.image) as im:
                    im.thumbnail((205, 230))
                    im = im.convert('RGBA')
                    self._photo = ImageTk.PhotoImage(im)
            else:
                pic = tk.PhotoImage(file=str(a.image))
                scale = max(1, (max(pic.width(), pic.height()) + 199) // 200)
                self._photo = pic.subsample(scale)
            self.preview_label.configure(image=self._photo, text='')
        except Exception:
            self._photo = None
            self.preview_label.configure(image='', text='Vorschau nicht verfügbar')

    def choose_workflow(self):
        path = filedialog.askopenfilename(title='ComfyUI API-JSON wählen',
                                          initialdir=str(BASE/'workflows'),
                                          filetypes=[('ComfyUI-Workflow', '*.json'), ('Alle Dateien', '*.*')])
        if path:
            self.workflow_var.set(path)
            self.inspect_workflow()

    def inspect_workflow(self):
        path = self.workflow_var.get().strip()
        try:
            flow = Workflow(path)
            images = flow.image_options()
            outputs = flow.output_options()
            self.img_options = {o.label: o.id for o in images}
            self.out_options = {o.label: o.id for o in outputs}
            self.image_combo['values'] = list(self.img_options)
            self.output_combo['values'] = list(self.out_options)
            for combo, options, saved in ((self.image_combo, images, self.settings.image_node),
                                          (self.output_combo, outputs, self.settings.output_node)):
                match = next((o.label for o in options if o.id == saved), None)
                if not match and len(options) == 1:
                    match = options[0].label
                combo.set(match or '')
            self.workflow_info.set(f'{len(flow.graph)} Nodes · {len(images)} Bild-Kandidaten · '
                                   f'{len(outputs)} GLB-Export-Kandidaten. '
                                   'Bei mehreren Kandidaten bitte manuell auswählen.')
            self._log('Workflow analysiert: ' + Path(path).name)
        except Exception as exc:
            self.workflow_info.set('Workflow-Fehler: ' + str(exc))
            self._log('Workflow-Fehler: ' + str(exc), True)

    def save_workflow(self):
        try:
            s = self._settings_from_form()
            s.workflow_path = self.workflow_var.get().strip()
            s.image_node = self.img_options.get(self.image_node_var.get(), self.image_node_var.get().strip())
            s.output_node = self.out_options.get(self.output_node_var.get(), self.output_node_var.get().strip())
            s.image_input = self.image_input_var.get().strip()
            s.prefix_input = self.prefix_input_var.get().strip()
            Workflow(s.workflow_path).resolve(s.image_node, s.output_node,
                                               s.image_input, s.prefix_input)
            save_settings(s)
            self.settings = s
            self._log('Workflow gespeichert und geprüft.')
            messagebox.showinfo('Workflow', 'API-Workflow ist für den Testlauf vorbereitet.')
        except Exception as exc:
            messagebox.showerror('Workflow', str(exc))

    def _settings_from_form(self):
        values = {k: v.get().strip() for k,v in self.svars.items()}
        try:
            for k in ('cpu_limit', 'ram_limit', 'gpu_limit', 'vram_limit', 'gpu_index',
                      'max_resource_wait_seconds', 'max_job_seconds'):
                values[k] = int(values[k])
            for k in ('cooldown_seconds', 'poll_seconds'):
                values[k] = float(values[k].replace(',', '.'))
        except ValueError:
            raise ValueError('Bitte gültige Zahlen in den Leistungseinstellungen verwenden.') from None
        s = Settings(**{**vars(self.settings), **values})
        s.gate_resources = self.gate_var.get()
        s.backup_on_replace = self.backup_var.get()
        s.root = self.root_var.get().strip()
        s.validate()
        return s

    def save_config(self):
        try:
            if self.runner and self.runner.running:
                raise ValueError('Einstellungen erst nach Abschluss/Pause des Batch ändern.')
            s = self._settings_from_form()
            save_settings(s)
            self.settings = s
            self._log('Leistungseinstellungen gespeichert; Cooldown: ' + str(s.cooldown_seconds) + ' s')
            messagebox.showinfo('Einstellungen', 'Einstellungen gespeichert.')
        except Exception as exc:
            messagebox.showerror('Einstellungen', str(exc))

    def test_connection(self):
        try:
            s = self._settings_from_form()
        except Exception as exc:
            messagebox.showerror('ComfyUI', str(exc)); return
        self.connection.set('ComfyUI: prüfe Verbindung…')
        def task():
            try:
                response = ComfyClient(s).ping()
                self.events.put(('connection', (True, 'ComfyUI ist erreichbar (System-API antwortet).')))
            except Exception as exc:
                self.events.put(('connection', (False, 'ComfyUI nicht erreichbar: ' + str(exc))))
        threading.Thread(target=task, daemon=True).start()

    def _runner_notify(self, kind, msg):
        self.events.put((kind, msg))

    def start_batch(self):
        if self.runner and self.runner.running:
            messagebox.showwarning('Batch', 'Es läuft bereits eine Verarbeitung.'); return
        if self.scanning:
            messagebox.showwarning('Scanner', 'Ordner-Scan abschliessen lassen.'); return
        if not self.selected:
            messagebox.showinfo('Auswahl', 'Bitte mindestens ein PNG auswählen.'); return
        existing = [a for a in self.assets if a.rel in self.selected and a.target.exists()]
        question = (f'{len(self.selected)} Assets ausgewählt, davon {len(existing)} mit vorhandener GLB.\n\n'
                    'Vorhandene GLBs der AUSGEWÄHLTEN Assets ersetzen?\n\n'
                    'JA: neu generieren und nach erfolgreicher Prüfung sicher ersetzen.\n'
                    'NEIN: vorhandene GLBs überspringen.\n'
                    'ABBRECHEN: nichts starten.')
        replace = messagebox.askyesnocancel('GLB-Überschreibmodus', question, icon='warning', default='no')
        if replace is None:
            return
        try:
            self.settings = self._settings_from_form()
            self.settings.workflow_path = self.workflow_var.get().strip()
            self.settings.image_node = self.img_options.get(self.image_node_var.get(), self.image_node_var.get().strip() or self.settings.image_node)
            self.settings.output_node = self.out_options.get(self.output_node_var.get(), self.output_node_var.get().strip() or self.settings.output_node)
            self.settings.image_input = self.image_input_var.get().strip()
            self.settings.prefix_input = self.prefix_input_var.get().strip()
            save_settings(self.settings)
            self.runner = BatchRunner(self.settings, self._runner_notify)
            self._replace_mode = replace
            self._run_started_at = time.time()
            self.runner.start(sorted(self.selected), replace=replace)
            self._log(f'Batch gestartet: {len(self.selected)} Assets, '
                      + ('mit Überschreiben' if replace else 'GLB überspringen'))
            self.notebook.select(self.assets_tab)
        except Exception as exc:
            messagebox.showerror('Batch nicht gestartet', str(exc))

    def pause(self):
        if self.runner and self.runner.running:
            self.runner.pause()
        else:
            self._log('Kein aktiver Batch.')

    def reset_jobs(self):
        if not self.selected:
            messagebox.showinfo('Auswahl', 'Erst Assets auswählen.'); return
        if not messagebox.askyesno('Jobs zurücksetzen',
                 'Job-/Auftrags-IDs der markierten Assets aus der aktiven Warteschlange lösen?\n'
                 'Vorhandene GLBs werden NICHT gelöscht.\n'
                 'Ein bisheriger ComfyUI-Auftrag könnte noch laufen und erneut gestartet werden.'):
            return
        try:
            root = self.root_var.get().strip()
            if not self.runner or str(self.runner.root) != str(Path(root).resolve()):
                self.runner = BatchRunner(Settings(**{**vars(self.settings), 'root': root}), self._runner_notify)
            self.runner.reset(list(self.selected))
            self._refresh_table()
        except Exception as exc:
            messagebox.showerror('Zurücksetzen', str(exc))

    def _close(self):
        if self.runner and self.runner.running:
            if not messagebox.askyesno('Batch läuft',
                      'Batch läuft noch. Anwendung beenden?\n'
                      'ComfyUI kann den aktuellen Auftrag weiterrechnen.\n'
                      'Die Auftrags-ID bleibt gespeichert und wird beim Neustart wieder aufgenommen.'):
                return
            self.runner.pause()
        self._closed.set()
        self.destroy()


def run():
    app = App()
    app.mainloop()
