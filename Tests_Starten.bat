@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set "PY=py -3"
if exist ".venv\Scripts\python.exe" (
 set "PY=.venv\Scripts\python.exe"
) else (
 py -3 --version >nul 2>&1
 if errorlevel 1 set "PY=python"
)
%PY% -m unittest discover -s tests -v
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (echo Alle Offline-Tests erfolgreich.) else (echo Tests fehlgeschlagen. Fehler oben beachten.)
pause
exit /b %RC%
