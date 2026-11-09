@echo off
setlocal
cd /d "%~dp0" || exit /b 1
if not exist "venv\Scripts\python.exe" (
    call setup.bat
    if errorlevel 1 goto failed
)
"venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
"venv\Scripts\python.exe" bot.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo Kirbo could not start. Check the error above.
pause
exit /b 1
