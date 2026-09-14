@echo off
rem ============================================================================
rem  Debug launcher: keeps the console window open so you can read the output.
rem  Same as start.bat but interactive and verbose.
rem
rem  Start the auto login manually and watch what happens:
rem      start_debug.bat
rem  Just diagnose the portal (no credentials needed, never logs in):
rem      start_debug.bat --probe
rem
rem  NOTE: keep this file ASCII-only (see the note in start.bat).
rem ============================================================================
setlocal
set "HERE=%~dp0"
set "PYEXE="
set "PYARGS="

if exist "%HERE%.venv\Scripts\python.exe" (
    set "PYEXE=%HERE%.venv\Scripts\python.exe"
    goto :found
)
where py >nul 2>nul && set "PYEXE=py" && set "PYARGS=-3" && goto :found
where python >nul 2>nul && set "PYEXE=python" && goto :found
for /d %%V in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%~V\python.exe" (
        set "PYEXE=%%~V\python.exe"
        goto :found
    )
)
for /d %%V in ("C:\Python3*") do (
    if exist "%%~V\python.exe" (
        set "PYEXE=%%~V\python.exe"
        goto :found
    )
)

echo [ERROR] Python was not found. Install Python 3.8+ first.
pause
exit /b 2

:found
"%PYEXE%" %PYARGS% "%HERE%login.py" --verbose %*
echo.
echo ---- exit code: %ERRORLEVEL% ----
echo log file: %LOCALAPPDATA%\CampusNetAutoLogin\login.log
pause
exit /b %ERRORLEVEL%
