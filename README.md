# AssetFoundry 1.0.0

**Local 3D Asset Pipeline** – eigenständige Windows-Anwendung zur rekursiven **PNG → GLB**-Batch-Generierung über eine **lokal laufende ComfyUI-Instanz**.

**Status:** ausführbares Testpaket. Scanner, Dateischutz, Batch-Steuerung, Wiederaufnahme, HTTP-API und Oberfläche sind implementiert und offline getestet. Ein echter 3D-/GPU-Lauf wurde **nicht** durchgeführt, weil der konkrete ComfyUI-Workflow und die ComfyUI-Installation des Nutzers nicht vorliegen. Die Praxisprüfung mit dem eigenen API-Workflow ist der verbleibende Integrationstest.

## Start in 5 Schritten

1. ZIP an einen beliebigen Ort auf Windows entpacken, z. B. `C:\AssetFoundry`.
2. ComfyUI lokal starten und sicherstellen, dass es erreichbar ist (typisch `http://127.0.0.1:8188`). Die 3D-Modelle und benötigten Custom Nodes müssen **bereits in ComfyUI installiert und funktionstüchtig** sein.
3. Den funktionierenden ComfyUI-Bild-zu-3D-Workflow im **API-Format** exportieren und unter **`workflows\API.json`** speichern. Falls der Dateiname anders lautet, kann er im Tab **Workflow** ausgewählt werden. Es ist **keine Cloud-API** erforderlich.
4. **`Start.bat`** doppelklicken. Beim ersten Start erzeugt es eine isolierte `.venv` und installiert `psutil` (CPU/RAM-Monitoring) und `Pillow` (Bildvorschau). Python 3.10+ mit Tkinter wird vorausgesetzt. Für die einmalige Installation dieser optionalen Pakete ist gegebenenfalls Internet nötig; danach ist der eigentliche Betrieb lokal. Die App kann ohne diese Extras starten, aber mit eingeschränkter Überwachung/Vorschau.
5. Im Tab **Workflow** auf **Analysieren** klicken, bei Bedarf Eingangs-/Export-Node wählen, **Workflow-Zuordnung speichern**. Im Tab **Leistung & Einstellungen** ComfyUI prüfen und 5-Sekunden-Cooldown / Lastgrenzen einstellen. Im Tab **Assets & Batch** Hauptordner auswählen, PNGs prüfen, Assets auswählen und Verarbeitung starten.

### Der erste praktische Test

Lege beispielsweise an:

```text
D:\3D-Assets\
    Baum.png
    Baum.glb         <- bereits vorhanden: standardmässig übersprungen
    Natur\
        Farn.png     <- wird generiert
        Felsen\
            Stein.png <- wird generiert
```

Für den Anfang am besten **ein einzelnes PNG ohne GLB** auswählen. ComfyUI soll es verarbeiten und genau **eine gültige GLB-Datei** produzieren. AssetFoundry lädt die GLB über die lokale API ab und speichert sie als `Farn.glb` im Bildordner.

**Export aus ComfyUI:** Benötigt wird das **API-Prompt-JSON**, nicht das normale UI-Workflow-JSON mit `nodes` und `links`-Listen. Der Export-Node muss die GLB tatsächlich **speichern** (typisch `SaveGLB` oder `Save3DAdvanced`). Ein reiner 3D-Vorschau-Node reicht nicht. Der Workflow muss zum ausgewählten PNG-Eingang verdrahtet sein und genau **ein GLB pro Auftrag** im gewählten Export-Node ausgeben. Der Batch ändert nur Dateiname des Bild-Eingangs und einen vorhandenen `filename_prefix`-Parameter; andere Modellparameter kommen aus deinem API-JSON. Bei mehreren Bild- oder Export-Nodes lassen sie sich im Tab Workflow auswählen; notfalls den Node-ID-Text eingeben.

## Dateiregeln

- Es werden rekursiv **nur `.png`/`.PNG`** gescannt, Unterordner bleiben erhalten.
- Pro Bild: **gleicher Ordner + gleicher Basisname + `.glb`**.
- Bereits vorhandene GLBs werden erkannt; der Batch fragt beim Start, ob **ausgewählte** bestehende GLBs überschrieben werden dürfen. **Nein** überspringt sie.
- Übertragene Ergebnisse werden zunächst im **Zielordner als `.part`** gespeichert, auf glTF-2.0-Grundstruktur geprüft und erst danach veröffentlicht.
- Standardmässig wird bei Überschreiben ein Backup nach **`<Assetordner>\.assetfoundry\backups\...`** erstellt. Falls Download/Validierung scheitern, bleibt die Original-GLB unberührt.
- Ungültige existierende GLBs werden angezeigt und ohne explizit bestätigtes Überschreiben ebenfalls nicht angetastet.
- Dateinamenskollisionen und symbolische Links werden gesperrt.
- Der Scanner ignoriert den eigenen `.assetfoundry`-Ordner.
- Bildsignatur wird vor dem Upload geprüft, maximales PNG 100 MiB. GLB-Download maximal 4 GiB.
- **GLB-Validierung ist eine strukturelle Prüfung, keine Zusicherung visueller Modellqualität**. Ein echtes Testmodell in einer 3D-Anwendung öffnen, um Mesh, Textur, Orientierung und Massstab zu prüfen.

## Leistung & Einstellungen

Die Anwendung zeigt nach Möglichkeit CPU, RAM, GPU-Auslastung und VRAM (Belegung und Prozent). CPU/RAM via `psutil`, GPU/VRAM via lokalem **NVIDIA-SMI**. Fehlen diese Daten (z. B. AMD/Intel, fehlendes `nvidia-smi`), wird **„–“** angezeigt und der entsprechende Grenzwert wird nicht erzwungen; es werden keine Werte erfunden.

| Einstellung | Vorgabe | Wirkung |
|---|---:|---|
| Cooldown zwischen erfolgreichen Generierungen | **5 s** | Pause vor dem nächsten Asset |
| CPU-Schwelle | 90 % | Start neuer Aufgaben bei hoher CPU-Last zurückhalten |
| RAM-Schwelle | 90 % | Neue Jobs bei hoher RAM-Belegung zurückhalten |
| GPU-Schwelle | 95 % | Neue Jobs bei hoher NVIDIA-GPU-Auslastung zurückhalten |
| VRAM-Schwelle | 95 % | Neue Jobs bei hoher VRAM-Belegung zurückhalten |
| GPU-Index | 0 | **Nur Monitoring** dieser NVIDIA-GPU |
| Maximale Wartezeit wegen hoher Last | 1800 s | Danach keine weiteren Jobs starten, Diagnose anzeigen |
| Maximale Wartezeit pro ComfyUI-Auftrag | 3600 s | Auftrag nicht doppelt senden; später fortsetzen |
| Polling-Intervall | 3 s | Statusabfragen bei ComfyUI |
| Vor Überschreiben Backup | Aktiv | Alte GLB sichern |

**Wichtig: Die Lastgrenzen gelten nur *vor* dem Einreichen des nächsten Auftrags.** ComfyUI arbeitet unabhängig in seinem eigenen Prozess. AssetFoundry kann weder den laufenden ComfyUI-Prozess auf einen bestimmten CPU-/GPU-Prozentsatz deckeln noch ohne Neustart dessen GPU wechseln. Für harte GPU-/VRAM-Konfigurationen die Startparameter und Einstellungen von ComfyUI selbst nutzen. Die angegebene GPU-Auswahl in AssetFoundry wählt ausschliesslich das zu **überwachende** Gerät.

Verbindung: standardmässig nur `127.0.0.1:8188`. Andere lokale Ports sind möglich; entfernte Server werden in Version 1.0 bewusst nicht zugelassen.

## Abbrüche und Neustart

- **Pause** stoppt weitere Uploads/Einreichungen zwischen den Assets; ein laufender ComfyUI-Auftrag wird **nicht abgebrochen** und die App versucht weiterhin, ihn abzuholen.
- Wird AssetFoundry während der Berechnung beendet, kann ComfyUI weiterlaufen. Bei erneutem Start denselben Ordner wählen und das Asset erneut markieren. AssetFoundry verwendet zunächst die gespeicherte **prompt_id** und sucht in `/history` und `/queue`; sie startet **nicht blind** eine zweite Generierung.
- Wenn eine Einreichung/Abfrage unklar ist, bleibt der Status **unklar**. In ComfyUI prüfen und erst dann bei Bedarf über **Fehler/Job zurücksetzen…** erneut freigeben.
- Wenn das PNG seit Auftragserstellung verändert wurde, wird der alte Auftrag nicht stillschweigend als neues Asset veröffentlicht.
- Job-IDs und Status liegen im Hauptordner unter **`.assetfoundry\jobs.json`**. Diese Datei bei einem laufenden Projekt **nicht löschen**. Beschädigte Zustandsdateien werden nicht automatisch überschrieben.
- Ein Betriebssystem-Lock unter `.assetfoundry\batch.lock` verhindert parallele Batch-Verarbeitung desselben Hauptordners aus mehreren Fenstern.

## Modulare Projektstruktur

```text
AssetFoundry/
├─ Start.bat                      Windows-Start, verwaltet .venv
├─ Tests_Starten.bat              Offline-Tests unter Windows
├─ requirements.txt               optionale Extras: psutil, Pillow
├─ README.md                      diese Anleitung
├─ assetfoundry/
│  ├─ __main__.py                  App-Einstieg
│  ├─ ui.py                        Tkinter-Oberfläche und Event-Queue
│  ├─ models.py                    Konfiguration + Domain-Modelle
│  ├─ assets.py                    rekursiver PNG-Scanner
│  ├─ state.py                     Job-Store + Folder-Lock
│  ├─ workflow.py                  ComfyUI-API-JSON-Mapping
│  ├─ comfy.py                     lokaler HTTP-Client
│  ├─ resources.py                 Metriken + Drosselung
│  ├─ output.py                    GLB-Prüfung + atomarer Export
│  └─ batch.py                     Orchestrierung, Recovery, Soft-Pause
├─ workflows/
│  ├─ HIER_API_JSON_ABLEGEN.txt
│  └─ API.json                     <--- HIER eigenen Workflow ablegen
└─ tests/
   └─ test_assetfoundry.py        Scanner, Workflow, State, HTTP, Output, Resource, Batch
```

Die Module sind eigenständig. **ComfyUI ist nur ein Backend**, die Dateiverwaltung und Batch-Engine enthalten keine TRELLIS.2-/Pixal3D-spezifische Logik. Bei einem neuen Modell muss normalerweise lediglich die Workflow-Datei geändert werden.

## Tests

Ohne ComfyUI und ohne GPU:

```powershell
cd C:\AssetFoundry
python -m unittest discover -s tests -v
```

Oder `Tests_Starten.bat` doppelklicken. Die Tests benutzen einen lokalen simulierten HTTP-Server, erzeugen eine kleine echte GLB-Testdatei und prüfen den kompletten Upload→Prompt→History→GLB-Download→Zieldatei-Prozess. Sie greifen nicht auf Cloud-Dienste zu.

**Echte Integration:** Windows-Tkinter-Fenster, externe NVIDIA-Treiber/`nvidia-smi` und der konkrete 3D-ComfyUI-Workflow müssen auf deinem Windows-PC geprüft werden. Modellqualität, Generationstempo und tatsächliche GPU-/RAM-Peaks hängen vom installierten Workflow ab.

## Daten & Diagnose

- Allgemeine Einstellungen: `%LOCALAPPDATA%\AssetFoundry\settings.json`
- Protokoll: `%LOCALAPPDATA%\AssetFoundry\activity.log`
- Status und Backups: `<Hauptordner>\.assetfoundry\...`
- PNGs werden ausschliesslich an die konfigurierte **lokale** ComfyUI-Instanz übertragen; keine Cloud-Tokens oder Meshy-Anbindung.
- Bei einem Bug: Protokoll, anonymisierte `jobs.json` und den *API*-Workflow auf Fehler prüfen. Beachten: Dateinamen und lokale Pfade können personenbezogene Informationen enthalten.

### Referenzen (ComfyUI API)

- https://docs.comfy.org/development/comfyui-server/comms_routes
- https://github.com/Comfy-Org/ComfyUI/blob/master/script_examples/websockets_api_example.py
- https://github.com/Comfy-Org/embedded-docs/blob/main/comfyui_embedded_docs/docs/SaveGLB/en.md
