@echo off
setlocal
cd /d "%~dp0"

:: Check for virtual environment with windowless pythonw.exe
if exist "%~dp0venv\Scripts\pythonw.exe" (
    start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0main.py"
    exit
)

if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
    exit
)

:: Error display if venv does not exist
echo ============================================================
echo [ERROR] Virtual environment was not found in .\venv or .\.venv
echo.
echo Please create your virtual environment before running:
echo   py -3.11 -m venv venv
echo   .\venv\Scripts\Activate.ps1
echo   python -m pip install -r requirements.txt
echo ============================================================
echo.
pause
exit /b 1
