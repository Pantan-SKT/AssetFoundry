# Architektur – AssetFoundry 1.1

## Architekturprinzip

- **Domain & Safety:** `assets.py`, `output.py`, `models.py`, `state.py` bleiben unabhängig von der Oberfläche.
- **Engine/Adapter:** `workflow.py` und `comfy.py` übersetzen API-JSON, Bilder, Prompts, Ergebnisreferenzen.
- **Orchestrierung:** `batch.py` ist die einzige Stelle, die Jobs erstellt, fortsetzt, validiert und GLBs publiziert.
- **Dashboard Controller:** `dashboard.py` besitzt UI-Auswahl, Scan-Caches, Meldungen und Telemetrie, delegiert Backend-Aktionen.
- **Transport:** `webapp.py` liefert eine lokal authentifizierte API und statische Frontend-Dateien.
- **Frontend:** `frontend/*` hat kein Dateisystemrecht; Dateidialoge, Scanner, Jobs, Backups und GPU-Monitoring laufen nur in Python.

## Datenfluss

```text
Windows Start.bat
   ↓
Python: 127.0.0.1:<zufälliger Port> + Sitzungstoken
   ↕ lokale HTTP/JSON API
Browser-App-Fenster: Dashboard + Asset-Liste + Workflow + System
   ↕
DashboardController (Selection, Log, Scan, Settings)
   ↕
BatchRunner → Workflow(API.json) → ComfyClient → ComfyUI lokal
   ↓                                            ↓
JobStore (prompt_id)  ← History/Queue ←   Prompt-Verarbeitung
   ↓
GLB-Download → temporäre GLB → Validator → Backup → Zieldatei
```

## Lebenszyklus

1. Ordnerwahl löst asynchron einen rekursiven Scan aus, mit Status je PNG/GLB.
2. Auswahl wird durch den Controller verwaltet und vor dem Start erneut durch `BatchRunner` validiert.
3. Workflow aus `workflows/API.json` wird als API-JSON geprüft. Bild- und Save-Node werden anhand der Einstellungen ausgewählt.
4. Bereits gesendete `prompt_id`s werden beim Fortsetzen geprüft und niemals automatisch neu vergeben.
5. Der Last-Gate blockiert nur vor der Einreichung; der Cooldown wirkt zwischen generierten Assets.
6. GLB-Export wird sicher atomar in den jeweiligen Quell-Unterordner veröffentlicht.

## Erweiterbarkeit

Neue Engines sollen einen eigenen Adapter erhalten; sie verändern **nicht** `assets.py`, `output.py` oder die UI. Für mehrere GPU-Geräte oder parallele GPU-Jobs muss das serialisierte Batch- und Lock-Konzept gezielt überarbeitet werden; das ist bewusst kein Teil von v1.1.

## Einschränkungen

- Keine direkte 3D-Vorschau, nur PNG-Vorschau und GLB-Status. Visuelle GLB-Qualität extern prüfen.
- Bekannte ComfyUI-Save-Nodes bzw. kompatible History-GLB-Ausgaben benötigt.
- Windows-App-Fenster via Edge/Chrome im App-Modus; ohne kompatiblen Browser normaler lokaler Browser-Tab.
- API-Workflow vom Nutzer erforderlich; kein konkretes Pixal3D/TRELLIS.2-JSON enthalten.
