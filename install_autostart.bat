@echo off
rem ============================================================================
rem  Enable "auto login at sign-in".
rem
rem  This only creates a shortcut inside the Windows Startup folder.
rem  It does NOT modify the registry and does NOT install a service.
rem
rem  Usage:
rem      install_autostart.bat          one-shot login at sign-in (start.bat)
rem      install_autostart.bat watch    resident watcher (start_watch.bat)
rem
rem  Use "watch" if your campus network drops after sleep / screen-off and you
rem  are tired of logging in by hand: the watcher keeps running and re-logs-in.
rem
rem  Startup folder: Win + R  ->  shell:startup
rem
rem  NOTE: keep this file ASCII-only (see the note in start.bat).
rem ============================================================================
setlocal
set "HERE=%~dp0"
set "WATCH="

if /i "%~1"=="watch" set "WATCH=-Watcher"

if "%WATCH%"=="" (
    if not exist "%HERE%start.bat" (
        echo [ERROR] start.bat not found next to this script.
        pause
        exit /b 2
    )
) else (
    if not exist "%HERE%start_watch.bat" (
        echo [ERROR] start_watch.bat not found next to this script.
        pause
        exit /b 2
    )
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%tools\autostart.ps1" %WATCH%
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo [ERROR] install failed with code %RC%.
    pause
    exit /b %RC%
)

echo.
echo  Auto start is now enabled. It will run every time you sign in to Windows.
echo  To undo it, run uninstall_autostart.bat
echo.
pause
exit /b 0
