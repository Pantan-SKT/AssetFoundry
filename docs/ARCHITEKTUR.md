# Architektur – 3D Asset Generator Batch 1.2.0 (AssetFoundry)

## Architekturprinzip

- **Domain & Safety:** `assets.py`, `output.py`, `models.py`, `state.py` bleiben unabhängig von der Oberfläche.
- **Engine/Adapter:** `workflow.py` und `comfy.py` übersetzen API-JSON, Bilder, Prompts, Ergebnisreferenzen.
- **Orchestrierung:** `batch.py` ist die einzige Stelle, die Jobs erstellt, fortsetzt, validiert und GLBs publiziert.
- **Dashboard Controller:** `dashboard.py` besitzt UI-Auswahl, Scan-Caches, Meldungen und Telemetrie, delegiert Backend-Aktionen.
- **Transport:** `webapp.py` liefert eine lokal authentifizierte API und statische Frontend-Dateien.
- **Diagnose:** `diagnostics.py` erstellt erst nach Benutzeraktion ein in Grösse begrenztes ZIP; weder Bilder noch GLBs und keine originalen Workflow-JSON-Daten. Fehler-Stacktraces und ComfyUI-Meldungen werden begrenzt als `errors.log` gespeichert.
- **Zeitdaten:** `state.py` kapselt `RunStore` mit Crash-sicheren Metadaten des letzten Batchs (`.assetfoundry/last-run.json`); `Job.started` und `Job.finished` enthalten Zeitstempel pro Asset.
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
7. Beginn, Ende und Heartbeat des Batches werden im `RunStore` abgelegt. Das Frontend berechnet die Anzeige sekündlich aus Zeitstempeln, sodass der Server keine sekündlichen Schreiboperationen braucht.
8. Der ZIP-Dump wird nur nach Bestätigung über die authentifizierte lokale `/api/diagnostics`-Route erzeugt.

## Erweiterbarkeit

Neue Engines sollen einen eigenen Adapter erhalten; sie verändern **nicht** `assets.py`, `output.py` oder die UI. Für mehrere GPU-Geräte oder parallele GPU-Jobs muss das serialisierte Batch- und Lock-Konzept gezielt überarbeitet werden; das ist bewusst kein Teil von v1.1.

## Einschränkungen

- Keine direkte 3D-Vorschau, nur PNG-Vorschau und GLB-Status. Visuelle GLB-Qualität extern prüfen.
- Bekannte ComfyUI-Save-Nodes bzw. kompatible History-GLB-Ausgaben benötigt.
- Windows-App-Fenster via Edge/Chrome im App-Modus; ohne kompatiblen Browser normaler lokaler Browser-Tab.
- API-Workflow vom Nutzer erforderlich; kein konkretes Pixal3D/TRELLIS.2-JSON enthalten.

## Thumbnail-Pipeline (1.1.2)

`dashboard.py` autorisiert die Relativpfade gegen seinen Scan-Index; `thumbnails.py` erzeugt bei Bedarf verkleinerte PNGs (maximal 128 × 128 Pixel, Transparenz erhalten). Der lokale HTTP-Server stellt sie über die token-geschützte `/api/thumbnail`-Route bereit. Der Browser lädt höchstens vier gleichzeitig, nur bei Sichtbarkeit der Zeilen. Server-Cache: 256 Einträge und 16 MiB; Browser-Cache: 200 Object-URLs. Kein Disk-Cache und keine Änderungen an den Originalbildern.

## Mehrsprachigkeit (1.2.0)

- Das Backend bleibt hinsichtlich Dateinamen, Datenstatus, Job-IDs und Batch-Logik sprachneutral. Diese Daten werden niemals übersetzt oder umgeschrieben.
- Frontend-Übersetzungen: `frontend/locales/de.json` und `en.json` (Wörterbuch mit deutschen Quelltexten als Schlüsseln) und `frontend/i18n.js`. Texte im HTML werden über erhaltene Original-Textknoten übersetzt, dynamische Status/Dialogs mit `t()` und Muster-Übersetzer für bekannte Logeinträge.
- Benutzerpräferenz `Settings.language` = `de` oder `en`. Eigene POST-Route `/api/language`, abgesichert durch Session-Token und Same-Origin-Prüfung. Darf während laufenden Generierungen aufgerufen werden; persistiert ausschließlich diese Präferenz.
- Die Sprachdateien sind als ausschließlich erlaubte lokale statische Ressourcen freigegeben; Dateipfade können hierüber nicht frei angegeben werden.
- Engine- und Diagnose-Rohdaten (inkl. ComfyUI-Zeilen/Stacktraces) bleiben unverändert, um Support und Wiederaufnahme nicht zu beeinträchtigen.
