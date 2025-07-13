@echo off
cd /d C:\Users\Recor\Kirbo

REM — create venv if it doesn’t already exist
if not exist venv (
    py -3.12 -m venv venv
)

REM — activate virtual environment
call venv\Scripts\activate

REM — upgrade pip, then install only binary wheels (no CMake builds)
pip install --upgrade pip
pip install --only-binary=:all: -r requirements.txt

REM — launch your bot
python bot.py

pause
