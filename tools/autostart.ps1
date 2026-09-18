# ============================================================================
#  Install / remove "auto login at sign-in" by putting a shortcut into the
#  Windows Startup folder.
#
#  Requirement (doc item 15): use the Startup folder ONLY - never touch the
#  registry, never install a service.
#
#  Usage:
#      powershell -NoProfile -ExecutionPolicy Bypass -File tools\autostart.ps1
#      powershell -NoProfile -ExecutionPolicy Bypass -File tools\autostart.ps1 -Watcher
#      powershell -NoProfile -ExecutionPolicy Bypass -File tools\autostart.ps1 -Remove
#
#  -Watcher installs the RESIDENT watcher (start_watch.bat) instead of the
#  one-shot login. Use it if the network drops after sleep / screen-off and you
#  are tired of logging in by hand: the watcher keeps checking and re-logs-in.
#
#  Keep this file ASCII-only: Windows PowerShell 5.1 reads .ps1 as ANSI unless
#  the file has a UTF-8 BOM, so non-ASCII text could be garbled.
# ============================================================================
[CmdletBinding()]
param(
    [switch]$Remove,
    [switch]$Watcher
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$startBat    = Join-Path $projectRoot $(if ($Watcher) { 'start_watch.bat' } else { 'start.bat' })
$startupDir  = [Environment]::GetFolderPath('Startup')
$shortcut    = Join-Path $startupDir 'CampusNetAutoLogin.lnk'

if ($Remove) {
    if (Test-Path -LiteralPath $shortcut) {
        Remove-Item -LiteralPath $shortcut -Force
        Write-Host "[OK] Auto start removed: $shortcut"
    } else {
        Write-Host "[INFO] Nothing to remove (no shortcut found)."
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $startBat)) {
    Write-Host "[ERROR] $startBat not found"
    exit 2
}

$shell = New-Object -ComObject WScript.Shell
$link  = $shell.CreateShortcut($shortcut)
$link.TargetPath       = $startBat
$link.WorkingDirectory = $projectRoot
$link.WindowStyle      = 7          # 7 = minimized, so nothing flashes at boot
$link.Description      = $(if ($Watcher) { 'Campus network auto login (watcher)' } else { 'Campus network auto login' })
$link.Save()

if (Test-Path -LiteralPath $shortcut) {
    Write-Host "[OK] Auto start installed."
    Write-Host "     Shortcut : $shortcut"
    Write-Host "     Target   : $startBat"
    if ($Watcher) {
        Write-Host "     The WATCHER will start at every sign-in and re-login automatically"
        Write-Host "     whenever the campus network drops (sleep / screen-off / idle kick)."
    } else {
        Write-Host "     This runs ONCE at every sign-in."
        Write-Host "     If your network drops after sleep, install the watcher instead:"
        Write-Host "         install_autostart.bat watch"
    }
    exit 0
}

Write-Host "[ERROR] Failed to create the shortcut."
exit 1
