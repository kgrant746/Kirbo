@echo off
cd /d C:\Users\Recor\Kirbo
python -m venv venv
call venv\Scripts\activate
pip install -r requirements.txt
python bot.py
pause