@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0" || goto :failed

if not exist "venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3 -m venv venv
    if errorlevel 1 (
        for /d %%D in ("%USERPROFILE%\AppData\Local\Programs\Python\Python3*") do (
            if exist "%%~fD\python.exe" set "PYTHON_EXE=%%~fD\python.exe"
        )
        if defined PYTHON_EXE (
            "!PYTHON_EXE!" -m venv venv
        ) else (
            python -m venv venv
        )
        if errorlevel 1 goto :failed
    )
)

echo Updating pip...
"venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed

echo Installing and updating dependencies...
"venv\Scripts\python.exe" -m pip install --upgrade --upgrade-strategy eager -r requirements.txt
if errorlevel 1 goto :failed

echo Setup complete. Run the bot with venv\Scripts\python.exe bot.py
exit /b 0

:failed
echo Setup failed. Check the error above and run setup.bat again.
exit /b 1
