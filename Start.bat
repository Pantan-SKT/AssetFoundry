@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PY=py -3"
%PY% --version >nul 2>&1
if errorlevel 1 set "PY=python"
%PY% --version >nul 2>&1
if errorlevel 1 (
 echo [FEHLER] Python 3.10+ nicht gefunden.
 echo Bitte Python fuer Windows installieren: https://www.python.org/downloads/windows/
 pause
 exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
 echo Erstelle isolierte Python-Umgebung ...
 %PY% -m venv ".venv"
 if errorlevel 1 goto :fail
)
if not exist ".venv\.assetfoundry-installed" (
 echo Installiere Monitoring und PNG-Vorschau ...
 ".venv\Scripts\python.exe" -m pip install -r requirements.txt
 if errorlevel 1 (
  echo [HINWEIS] Pakete konnten nicht installiert werden. Start mit Standardbibliothek.
 ) else (
  type nul > ".venv\.assetfoundry-installed"
 )
)
".venv\Scripts\python.exe" -m assetfoundry
if errorlevel 1 goto :fail
exit /b 0
:fail
 echo.
 echo AssetFoundry konnte nicht normal starten. Siehe Fehlermeldung oben.
 pause
 exit /b 1
