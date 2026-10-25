@echo off
setlocal
echo =========================================================
echo   Building Standalone Windows Executable (YTAutomationTool)
echo =========================================================

set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

if not exist ".venv\Scripts\pyinstaller.exe" (
    echo [ERROR] PyInstaller not found in .venv. Installing pyinstaller...
    call ".venv\Scripts\pip.exe" install pyinstaller
)

echo Cleaning previous build artifacts...
if exist "build" rmdir /s /q "build"
if exist "dist\YTAutomationTool" rmdir /s /q "dist\YTAutomationTool"

echo Running PyInstaller...
call ".venv\Scripts\pyinstaller.exe" --clean yt_tool.spec

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. Check the logs above for details.
    pause
    exit /b 1
)

echo Syncing configuration and account files to build folder...
if exist "settings.json" copy /y "settings.json" "dist\YTAutomationTool\settings.json" >nul
if exist "stats.json" copy /y "stats.json" "dist\YTAutomationTool\stats.json" >nul
if exist "accounts.txt" copy /y "accounts.txt" "dist\YTAutomationTool\accounts.txt" >nul
if exist "credentials.json" copy /y "credentials.json" "dist\YTAutomationTool\credentials.json" >nul

echo.
echo =========================================================
echo   Build Successful!
echo   Executable created at:
echo   %SCRIPT_DIR%dist\YTAutomationTool\YTAutomationTool.exe
echo =========================================================
pause
