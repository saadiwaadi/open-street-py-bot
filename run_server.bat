@echo off
echo Starting Lead Generation Backend Server...
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
    .venv\Scripts\python.exe -m uvicorn leadgen_backend.main:app --host 0.0.0.0 --port 8000 --reload
) else (
    python -m uvicorn leadgen_backend.main:app --host 0.0.0.0 --port 8000 --reload
)
pause
