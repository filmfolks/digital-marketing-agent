# Nexus launcher (Windows PowerShell).
#   .\start.ps1           first run: create .venv, install deps + Chromium, start the relay
#   .\start.ps1 -Lan      let your phone reach the relay (generates + saves a token if missing)
#   .\start.ps1 -Check    validate config and exit
param([switch]$Lan, [switch]$Check)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$venvOk = $false
if (Test-Path $py) { try { & $py -c "import sys" 2>$null; $venvOk = ($LASTEXITCODE -eq 0) } catch { $venvOk = $false } }
if (-not $venvOk) {
    # First run, or a .venv copied from another computer (e.g. a Mac via pendrive): rebuild it here.
    & (Join-Path $PSScriptRoot "scripts\setup_windows.ps1")
}
if (-not (Test-Path ".env") -and (Test-Path ".env.example")) { Copy-Item .env.example .env }
if ($Check) { & $py agent\nexus_relay.py --check-config; exit $LASTEXITCODE }
if ($Lan) {
    $line = (Select-String -Path .env -Pattern '^NEXUS_RELAY_TOKEN=(.+)$' -ErrorAction SilentlyContinue | Select-Object -Last 1)
    $token = if ($env:NEXUS_RELAY_TOKEN) { $env:NEXUS_RELAY_TOKEN } elseif ($line) { $line.Matches[0].Groups[1].Value.Trim('"', "'") } else { "" }
    if (-not $token) {
        $token = (& $py agent\nexus_relay.py --generate-token).Trim()
        $content = if (Test-Path .env) { Get-Content .env -Raw } else { "" }
        if ($content -match '(?m)^NEXUS_RELAY_TOKEN=') {
            $content = [regex]::Replace($content, '(?m)^NEXUS_RELAY_TOKEN=.*$', "NEXUS_RELAY_TOKEN=$token")
            Set-Content .env $content -NoNewline
        } else { Add-Content .env "NEXUS_RELAY_TOKEN=$token" }
        Write-Host "-> generated a relay token and saved it to .env"
    }
    Write-Host "-> phone token: $token"
    & $py agent\nexus_relay.py --lan
} else {
    & $py agent\nexus_relay.py
}
