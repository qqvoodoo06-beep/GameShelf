@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creating the Python environment / Создаю окружение Python...
    python -m venv .venv
)
echo Checking dependencies / Проверяю зависимости...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt -r requirements-build.txt
".venv\Scripts\python.exe" build.py --shortcut
echo.
pause
