@echo off
rem ============================================================================
rem  Disable "auto login at sign-in" by deleting the Startup folder shortcut.
rem  (Registry is never touched.)
rem
rem  NOTE: keep this file ASCII-only (see the note in start.bat).
rem ============================================================================
setlocal
set "HERE=%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%tools\autostart.ps1" -Remove
set "RC=%ERRORLEVEL%"

echo.
pause
exit /b %RC%
