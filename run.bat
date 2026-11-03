@echo off
setlocal
cd /d "%~dp0"

:: Check if debug / console mode requested
set "PYTHON_EXE=pythonw.exe"
if "%~1"=="--debug" set "PYTHON_EXE=python.exe"
if "%~1"=="--console" set "PYTHON_EXE=python.exe"
if "%~1"=="debug" set "PYTHON_EXE=python.exe"

:: Check for virtual environment in .\venv
if exist "%~dp0venv\Scripts\%PYTHON_EXE%" (
    if "%PYTHON_EXE%"=="python.exe" (
        "%~dp0venv\Scripts\python.exe" "%~dp0main.py" %*
        pause
        exit /b
    ) else (
        start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0main.py"
        exit
    )
)

:: Check for virtual environment in .\.venv
if exist "%~dp0.venv\Scripts\%PYTHON_EXE%" (
    if "%PYTHON_EXE%"=="python.exe" (
        "%~dp0.venv\Scripts\python.exe" "%~dp0main.py" %*
        pause
        exit /b
    ) else (
        start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0main.py"
        exit
    )
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
