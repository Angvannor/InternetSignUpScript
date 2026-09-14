# ============================================================================
#  Install / remove "auto login at sign-in" by putting a shortcut of start.bat
#  into the Windows Startup folder.
#
#  Requirement (doc item 15): use the Startup folder ONLY - never touch the
#  registry, never install a service.
#
#  Usage:
#      powershell -NoProfile -ExecutionPolicy Bypass -File tools\autostart.ps1
#      powershell -NoProfile -ExecutionPolicy Bypass -File tools\autostart.ps1 -Remove
#
#  Keep this file ASCII-only: Windows PowerShell 5.1 reads .ps1 as ANSI unless
#  the file has a UTF-8 BOM, so non-ASCII text could be garbled.
# ============================================================================
[CmdletBinding()]
param(
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$startBat    = Join-Path $projectRoot 'start.bat'
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
    Write-Host "[ERROR] start.bat not found at $startBat"
    exit 2
}

$shell = New-Object -ComObject WScript.Shell
$link  = $shell.CreateShortcut($shortcut)
$link.TargetPath       = $startBat
$link.WorkingDirectory = $projectRoot
$link.WindowStyle      = 7          # 7 = minimized, so nothing flashes at boot
$link.Description      = 'Campus network auto login'
$link.Save()

if (Test-Path -LiteralPath $shortcut) {
    Write-Host "[OK] Auto start installed."
    Write-Host "     Shortcut : $shortcut"
    Write-Host "     Target   : $startBat"
    Write-Host "     start.bat will now run every time you sign in to Windows."
    exit 0
}

Write-Host "[ERROR] Failed to create the shortcut."
exit 1
