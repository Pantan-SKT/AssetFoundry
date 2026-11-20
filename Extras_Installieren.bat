@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
 echo Bitte zuerst Start.bat ausfuehren.
 pause
 exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
 echo Die Installation der Extras ist fehlgeschlagen.
 pause
 exit /b 1
)
echo Extras erfolgreich installiert.
pause
