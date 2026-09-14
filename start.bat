@echo off
rem ============================================================================
rem  Campus network auto login - Windows startup entry point
rem
rem  Usage:
rem    * Double click this file to run the login once.
rem    * Put a shortcut of this file into the Startup folder for auto start
rem      (run install_autostart.bat, or Win+R -> shell:startup).
rem
rem  This script does NOT assume Python is on PATH. It searches, in order:
rem    1. the project's own virtualenv      .venv\Scripts\pythonw.exe
rem    2. the Python Launcher               pyw -3 / py -3
rem    3. pythonw.exe / python.exe          on PATH
rem    4. common install locations          %LOCALAPPDATA%\Programs\Python\Python3* ...
rem
rem  It prefers *pythonw*.exe so that no console window flashes at boot.
rem  All output goes to %LOCALAPPDATA%\CampusNetAutoLogin\login.log
rem
rem  NOTE: keep this file ASCII-only. cmd.exe reads batch files with the OEM
rem  code page, and non-ASCII text here would be mis-decoded and can break the
rem  script. All Chinese documentation lives in README.md instead.
rem ============================================================================

setlocal
set "HERE=%~dp0"
set "PYEXE="
set "PYARGS="

rem ---- 1) project virtualenv -------------------------------------------------
if exist "%HERE%.venv\Scripts\pythonw.exe" (
    set "PYEXE=%HERE%.venv\Scripts\pythonw.exe"
    goto :found
)

rem ---- 2) Python launcher (installed into C:\Windows by python.org) ---------
where pyw >nul 2>nul && set "PYEXE=pyw" && set "PYARGS=-3" && goto :found
where py  >nul 2>nul && set "PYEXE=py"  && set "PYARGS=-3" && goto :found

rem ---- 3) python on PATH ----------------------------------------------------
where pythonw >nul 2>nul && set "PYEXE=pythonw" && goto :found
where python  >nul 2>nul && set "PYEXE=python"  && goto :found

rem ---- 4) common install locations ------------------------------------------
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
for /d %%V in ("%ProgramFiles%\Python3*") do (
    if exist "%%~V\pythonw.exe" (
        set "PYEXE=%%~V\pythonw.exe"
        goto :found
    )
)

rem ---- not found: log it (never "pause": at boot there is no console) -------
set "LOGDIR=%LOCALAPPDATA%\CampusNetAutoLogin"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>nul
>>"%LOGDIR%\start_error.log" echo [%date% %time%] Python not found. Install Python 3.8+ or put pythonw.exe on PATH.
echo [ERROR] Python was not found. Please install Python 3.8+ from https://www.python.org/downloads/
echo         (tick "Add python.exe to PATH" during installation)
exit /b 2

:found
"%PYEXE%" %PYARGS% "%HERE%login.py" --quiet --non-interactive %*
exit /b %ERRORLEVEL%
