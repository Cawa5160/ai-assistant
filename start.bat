@echo off
chcp 65001 >nul
cd /d "%~dp0"

where pythonw >nul 2>nul
if errorlevel 1 (
    echo pythonw не найден - запускаю через обычный python (будет видно окно консоли).
    python main.py
) else (
    start "" pythonw main.py
)
