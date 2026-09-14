@echo off
rem ============================================================================
rem  Enable "auto login at sign-in".
rem
rem  This only creates a shortcut of start.bat inside the Windows Startup
rem  folder. It does NOT modify the registry and does NOT install a service.
rem
rem  Startup folder: Win + R  ->  shell:startup
rem
rem  NOTE: keep this file ASCII-only (see the note in start.bat).
rem ============================================================================
setlocal
set "HERE=%~dp0"

if not exist "%HERE%start.bat" (
    echo [ERROR] start.bat not found next to this script.
    pause
    exit /b 2
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%tools\autostart.ps1"
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo [ERROR] install failed with code %RC%.
    pause
    exit /b %RC%
)

echo.
echo  Auto start is now enabled. It will run start.bat at every Windows sign-in.
echo  To undo it, run uninstall_autostart.bat
echo.
pause
exit /b 0
