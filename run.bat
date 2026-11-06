@echo off
cd /d "%~dp0"
uv sync --python 3.12
if errorlevel 1 (
    echo.
    echo Cai dat thu vien that bai.
    pause
    exit /b 1
)
uv run --no-sync python app.py
pause
