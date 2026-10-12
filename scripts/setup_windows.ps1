# Nexus - Windows setup (UNTESTED on real Windows hardware; written for Windows 10/11 + PowerShell 5.1+).
# Creates .venv INSIDE this folder, installs requirements and Playwright Chromium.
# A virtualenv is tied to the machine/Python that made it, so after copying Nexus
# from another computer (pendrive, zip) ALWAYS run this once on the new laptop.
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
#   ... -Force        delete and rebuild .venv even if it looks fine
#   ... -NoBrowser    skip "playwright install chromium" (use your Chrome/Edge via NEXUS_BROWSER_CHANNEL)
param([switch]$Force, [switch]$NoBrowser)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -Path $Root

function Find-Python {
    foreach ($c in @(@("py", "-3"), @("python"), @("python3"))) {
        $exe = $c[0]
        if (Get-Command $exe -ErrorAction SilentlyContinue) {
            $args2 = @($c | Select-Object -Skip 1) + @("-c", "import sys; print(sys.version_info >= (3, 9))")
            $ok = (& $exe @args2 2>$null)
            if ($ok -eq "True") { return ,$c }
        }
    }
    throw "Python 3.9+ not found. Install it from https://www.python.org/downloads/windows/ (tick 'Add python.exe to PATH') and run this again."
}

$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
$healthy = $false
if ((Test-Path $venvPy) -and -not $Force) {
    try { & $venvPy -c "import sys" 2>$null; $healthy = ($LASTEXITCODE -eq 0) } catch { $healthy = $false }
}
if (-not $healthy) {
    if (Test-Path ".venv") {
        Write-Host "-> removing .venv (copied from another machine or broken)"
        Remove-Item -Recurse -Force ".venv"
    }
    $py = Find-Python
    $boot = $py[0]; $bootArgs = @($py | Select-Object -Skip 1)
    Write-Host "-> creating .venv with $($py -join ' ')"
    & $boot @bootArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
}
& $venvPy -m pip install -q --disable-pip-version-check --upgrade pip
& $venvPy -m pip install -q --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install -r requirements.txt failed" }
if (-not $NoBrowser) {
    & $venvPy -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { Write-Warning "playwright chromium download failed; set NEXUS_BROWSER_CHANNEL=msedge or chrome to use an installed browser" }
}
foreach ($d in @("logs", "shots", "data")) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
if (-not (Test-Path ".env") -and (Test-Path ".env.example")) { Copy-Item .env.example .env; Write-Host "-> created .env from .env.example (add your API keys)" }
& $venvPy agent\nexus_relay.py --check-config
Write-Host ""
Write-Host "Done. Start Nexus with start.bat (or .\start.ps1), then open http://127.0.0.1:8712/app/"
Write-Host "Auto-start at logon: powershell -ExecutionPolicy Bypass -File scripts\install_daemon_windows.ps1"
