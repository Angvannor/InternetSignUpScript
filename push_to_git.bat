@echo off
rem ============================================================================
rem  Push this project to your Git repository.
rem
rem  Usage:
rem      push_to_git.bat                       (push to the configured origin)
rem      push_to_git.bat https://.../repo.git  (add/update origin, then push)
rem
rem  IMPORTANT (measured on this campus network):
rem      HTTPS to the outside world is blocked until you are authenticated on
rem      the campus network. So log in FIRST (python login.py --setup), then
rem      run this script. "Basic connection was closed" / TLS errors mean you
rem      are still not authenticated.
rem
rem  NOTE: keep this file ASCII-only (see the note in start.bat).
rem ============================================================================
setlocal
set "HERE=%~dp0"
cd /d "%HERE%"

where git >nul 2>nul
if errorlevel 1 (
    echo [ERROR] git was not found on PATH. Install Git for Windows first.
    pause
    exit /b 2
)

if not exist ".git" (
    echo [1/4] git init
    git init || goto :failed
) else (
    echo [1/4] repository already initialised
)

if not "%~1"=="" (
    echo [2/4] setting origin to %~1
    git remote remove origin >nul 2>nul
    git remote add origin "%~1" || goto :failed
    goto :stage
)

git remote get-url origin >nul 2>nul
if errorlevel 1 goto :askremote
echo [2/4] origin = 
git remote get-url origin
goto :stage

:askremote
echo [2/4] no 'origin' remote configured.
set /p REMOTE_URL=Enter your Git repository URL (HTTPS or SSH): 
if "%REMOTE_URL%"=="" (
    echo [ERROR] no URL given, aborting.
    pause
    exit /b 2
)
git remote add origin "%REMOTE_URL%" || goto :failed

:stage
echo [3/4] git add + commit
git add -A || goto :failed
git commit -m "feat: campus network auto login tool (Dr.COM eportal)" || echo    (nothing new to commit)

echo [4/4] git push
git push -u origin HEAD || goto :failed

echo.
echo [OK] pushed.
pause
exit /b 0

:failed
echo.
echo [ERROR] a git command failed. Common causes:
echo   1. You are not logged in to the campus network, so HTTPS is blocked.
echo      Run:  python login.py --setup
echo   2. The remote needs credentials. Use a Personal Access Token as the
echo      password, or configure an SSH key.
echo   3. The remote repository does not exist yet - create it in the web UI.
pause
exit /b 1
