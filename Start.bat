@echo off
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PY=py -3"
%PY% --version >nul 2>&1
if errorlevel 1 set "PY=python"
%PY% --version >nul 2>&1
if errorlevel 1 (
 echo [FEHLER] Python 3.10+ nicht gefunden. https://www.python.org/downloads/
 pause
 exit /b 1
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
 echo [FEHLER] AssetFoundry benoetigt Python 3.10 oder neuer.
 pause
 exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
 echo [AssetFoundry] Virtuelle Python-Umgebung wird erstellt ...
 %PY% -m venv ".venv"
 if errorlevel 1 goto :fail
)
if not exist ".venv\.extras-1.1.2-ok" (
 echo [AssetFoundry] Installiere optionale Monitoring-/Vorschau-Pakete ...
 ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --timeout 8 --retries 1 -r requirements.txt
 if errorlevel 1 (
  echo [HINWEIS] Thumbnail-Paket Pillow nicht installiert. Spaeter Extras_Installieren.bat ausfuehren.
 ) else (
  type nul > ".venv\.extras-1.1.2-ok"
 )
)
echo.
echo [AssetFoundry] Starte lokales App-Fenster ...
echo [AssetFoundry] Zum Beenden im Fenster "Anwendung beenden" waehlen.
".venv\Scripts\python.exe" -m assetfoundry
if errorlevel 1 goto :fail
exit /b 0
:fail
echo.
echo [FEHLER] Start fehlgeschlagen. Fehlermeldungen oben pruefen.
pause
exit /b 1
