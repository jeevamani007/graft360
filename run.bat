@echo off
cd /d %~dp0backend
python -m pip install -r requirements.txt
echo Open http://127.0.0.1:8000 in your browser
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
