# Sicherheitsmodell

**Betriebsmodell:** Lokale Einzelbenutzer-Anwendung, kein Internetdienst und keine Mehrbenutzer-Webplattform.

- Der Server bindet ausschliesslich an die IPv4-Loopbackadresse **127.0.0.1**, mit zufälligem Port und zeitlich begrenztem Sitzungstoken (Lebensdauer des Prozesses). API-Requests ohne Token oder mit fremdem `Origin` werden abgewiesen. Statische Ressourcen sind bewusst öffentlich, enthalten aber keine privaten Daten.
- Nur `application/json` POSTs bis 64 KiB. Sicherheitseinstellungen für lokale ComfyUI-Adresse verweigern entfernte Hosts und HTTP-Redirects. Browser-Daten werden als Text ausgegeben, nicht ungeprüft als HTML interpretiert; HTML-Templates escapen Pfadnamen.
- **Wichtig:** Lokale Prozesse im selben Windows-Benutzerkontext sind nicht voreinander isoliert. Token-basierter Browser-Zugriff verhindert gewöhnliche Cross-Site-Angriffe, aber ersetzt nicht den Schutz des Windows-Kontos.
- Der Controller liest Vorschaubilder nur aus der vorher gescannten Assetliste; der Scanner blockiert symbolische Links. Der Reader limitiert PNG-Daten, Pillow begrenzt Pixelzahlen. Publikationen werden zusätzlich innerhalb des ausgewählten Rootverzeichnisses geprüft.
- Keine Cloud-Telemetrie, keine Fern-API, keine Drittanbieter-JavaScript-Dateien. Browser-CSP beschränkt Skripte, Styles, Bilder und Verbindungen auf lokale Quellen.
- Bestehende GLB-Dateien werden erst nach vollständiger Validierung ersetzt; optionale Backups schützen vor unbeabsichtigten Änderungen. Bei unklaren ComfyUI-Aufträgen keine automatische Doppelgenerierung.
- Native Dateidialoge und die manuelle Wahl lokaler Ordner erlauben bewusst den Zugriff auf Dateien des **angemeldeten Benutzers**. AssetFoundry daher nur aus vertrauenswürdigen Paketen starten.
- Vor öffentlicher Veröffentlichung des Repositories sicherstellen, dass `workflows/API.json`, `.assetfoundry/`, `.venv/`, lokale Assets und Logdateien nicht eingecheckt sind.

## Support- und Fehler-Dump

- ZIP-Export ausschliesslich durch eine aktive, lokal authentifizierte Benutzeraktion als `POST /api/diagnostics`. Es gibt keinen öffentlichen oder automatischen Upload.
- **Standard:** absolute Ordnerpfade werden maskiert. Dateinamen und relative Pfade in Fehlermeldungen können sichtbar bleiben; das ZIP vor dem Weitergeben prüfen.
- Auch im vollständigen Pfadmodus werden schlüsselartige Werte (`token`, `api_key`, `password`, `secret`, `authorization`) in Textfeldern maskiert; ein lokaler Export ist dennoch keine Garantie vollständiger Anonymisierung.
- Im Export fehlen ausdrücklich PNG/GLB-Dateien, das eigentliche `API.json`, Einstellungen-Dateien im Original und die Sitzungstoken der App.
- Begrenzte Protokollmengen (je maximal 700 KB und 10'000 Jobs) schützen vor übergrossen Support-ZIPs. Fehler-Logs liegen unter `%LOCALAPPDATA%\AssetFoundry\errors.log`.
- Vor dem Teilen in einer öffentlichen GitHub-Issue private Hinweise in relativen Asset-Namen, Logs und Stacktraces kontrollieren.

## Thumbnails (1.1.2)

Die Miniaturansicht ist nur mit Sitzungstoken für im Asset-Scan vorhandene, reguläre Dateien erreichbar. Die Komprimierung verwendet Pillow, begrenzt die Eingabebildfläche auf 64 Millionen Pixel und produziert nur max. 128 × 128 Pixel. Die Original-PNGs werden unverändert belassen und nicht in Diagnosearchive aufgenommen. Pillow fehlt: grosse Bilder werden bewusst nicht als Vorschau im Original übertragen.
