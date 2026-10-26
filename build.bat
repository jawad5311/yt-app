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

set STAGING_DIR=%SCRIPT_DIR%build\staging\YTAutomationTool
set DIST_DIR=%SCRIPT_DIR%dist\YTAutomationTool

echo Cleaning staging build directory...
if exist "%SCRIPT_DIR%build\staging" rmdir /s /q "%SCRIPT_DIR%build\staging"

echo Running PyInstaller in isolated staging...
call ".venv\Scripts\pyinstaller.exe" --clean --distpath "%SCRIPT_DIR%build\staging" yt_tool.spec

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. Check the logs above for details.
    pause
    exit /b 1
)

echo Syncing release files to destination...
if not exist "%DIST_DIR%" mkdir "%DIST_DIR%"
xcopy /s /y /i /d "%STAGING_DIR%" "%DIST_DIR%" >nul

echo [NOTE] Personal credentials and state are excluded from the release build.
echo [NOTE] The application will initialize settings with safe defaults on first launch.
if exist "accounts.example.txt" copy /y "accounts.example.txt" "%DIST_DIR%\accounts.example.txt" >nul
if exist "settings.example.json" copy /y "settings.example.json" "%DIST_DIR%\settings.example.json" >nul

echo.
echo =========================================================
echo   Build Successful!
echo   Executable created at:
echo   %DIST_DIR%\YTAutomationTool.exe
echo =========================================================
pause
