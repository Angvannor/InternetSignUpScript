@echo off
rem ============================================================================
rem  Resident watcher: keeps running and re-logs-in automatically whenever the
rem  campus network drops (after sleep / screen-off / being kicked offline).
rem
rem  Why you want this:
rem    The Windows Startup folder only runs at sign-in. It cannot react to
rem    "woke up from sleep and the network is gone". This process stays alive
rem    and checks every 30 seconds, so it covers sleep/resume, lock/unlock and
rem    idle-timeout disconnects.
rem
rem  Usage:
rem    * Double click to start watching (keep the window, or it runs hidden
rem      when launched through pythonw).
rem    * install_autostart.bat watch   -> auto start the watcher at sign-in.
rem
rem  NOTE: keep this file ASCII-only (see the note in start.bat).
rem ============================================================================

setlocal
set "HERE=%~dp0"
set "PYEXE="
set "PYARGS="

if exist "%HERE%.venv\Scripts\pythonw.exe" (
    set "PYEXE=%HERE%.venv\Scripts\pythonw.exe"
    goto :found
)
where pyw >nul 2>nul && set "PYEXE=pyw" && set "PYARGS=-3" && goto :found
where py >nul 2>nul && set "PYEXE=py" && set "PYARGS=-3" && goto :found
where pythonw >nul 2>nul && set "PYEXE=pythonw" && goto :found
where python >nul 2>nul && set "PYEXE=python" && goto :found
for /d %%V in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%~V\pythonw.exe" (
        set "PYEXE=%%~V\pythonw.exe"
        goto :found
    )
)
for /d %%V in ("C:\Python3*") do (
    if exist "%%~V\pythonw.exe" (
        set "PYEXE=%%~V\pythonw.exe"
        goto :found
    )
)

set "LOGDIR=%LOCALAPPDATA%\CampusNetAutoLogin"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>nul
>>"%LOGDIR%\start_error.log" echo [%date% %time%] Python not found (watcher).
echo [ERROR] Python was not found. Please install Python 3.8+ first.
exit /b 2

:found
set "LOGDIR=%LOCALAPPDATA%\CampusNetAutoLogin"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>nul

rem ---- pre-flight: make sure every module imports (see start.bat) ----------
"%PYEXE%" %PYARGS% "%HERE%tools\preflight.py" >"%LOGDIR%\preflight.log" 2>&1
if errorlevel 1 (
    >>"%LOGDIR%\start_error.log" echo [%date% %time%] preflight failed - see preflight.log
    exit /b 3
)

"%PYEXE%" %PYARGS% "%HERE%login.py" --watch --quiet %*
exit /b %ERRORLEVEL%
