# Localization / Mehrsprachigkeit — 3D Asset Generator Batch 1.2.0

Deutsch und Englisch sind im lokalen, offline-fähigen Frontend unterstützt.

## Architektur

- `assetfoundry/frontend/locales/de.json`: Deutsche Originalstrings als Quellkatalog
- `assetfoundry/frontend/locales/en.json`: Englische Übersetzungen mit identischen Schlüsseln
- `assetfoundry/frontend/i18n.js`: sichere Umschaltung, Übersetzung statischer Textknoten, HTML-Attribute, bekannte technische Ereignisse und Sprachmetadaten
- `assetfoundry/frontend/app.js`: Aufruf von `t()` für dynamische Meldungen; interne Werte und Pfade bleiben sprachneutral
- `assetfoundry/models.py`: validierte Einstellung `language` (`de`/`en`)
- `assetfoundry/dashboard.py`: persistente Änderung über `update_language()` während eines laufenden Batches
- `assetfoundry/webapp.py`: `/api/language` mit Sitzungstoken, eingeschränkter Auswahl der Sprachkataloge

## Neue Meldung übersetzen

1. Für HTML: Deutschen Quelltext ergänzen und den identischen Wortlaut in den JSON-Katalogen hinterlegen.
2. Für JavaScript: Dynamische sichtbare Zeichenketten über `t('Deutscher Quelltext')` übersetzen; Variablen und Benutzerpfade nicht in die Übersetzungsquelle aufnehmen.
3. Keine Übersetzung von Dateinamen, Modellnamen, Prompt-IDs, Dateipfaden, Job-Status-IDs oder ComfyUI-Rohfehlern. Programmstatus nur bei der Anzeige übersetzen.
4. Beide Kataloge auf denselben Schlüsselsatz prüfen, Tests ausführen, dann Browser-Ansichten/Dialogs in beiden Sprachen prüfen.

## API

`POST /api/language` mit `{ "language": "en" }` oder `{ "language": "de" }`. Erfordert dieselbe Session-Authentifizierung und Same-Origin-Prüfung wie die übrigen lokalen Endpunkte. Anderes wird mit HTTP 400 abgelehnt. Speichert die Präferenz sofort in der bestehenden Einstellungsdatei, ohne den laufenden Batch zu stoppen oder andere Einstellungen zu verändern.

## Migration

Vorherige `settings.json`-Dateien ohne `language` werden automatisch als Deutsch behandelt. Eine bestehende Datei wird nur beim expliziten Speichern ergänzt. Dateistruktur, Aufträge, Thumbnails, GLBs und Backups bleiben unverändert.

## Grenzen

ComfyUI liefert teils unstrukturierte Originalfehler und Tracebacks. Diese werden bewusst nicht automatisch übersetzt (und auch im Support-Dump unverändert belassen). Bekannte Anwendungsmeldungen erscheinen dagegen in der gewählten UI-Sprache.
