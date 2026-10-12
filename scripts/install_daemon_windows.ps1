# Nexus - start the relay automatically when you log on to Windows (UNTESTED on real Windows).
# Registers a per-user Task Scheduler task (no admin rights needed) that runs
#   .venv\Scripts\pythonw.exe agent\nexus_relay.py   (no console window)
# at logon and restarts it up to 3 times if it crashes.
#   powershell -ExecutionPolicy Bypass -File scripts\install_daemon_windows.ps1
#   ... -TaskName "Nexus Agent (client)"   second copy side by side
#   ... -StartupFolder                     fallback: shortcut in shell:startup instead of a task
#   ... -Print                             show what would be registered
param([string]$TaskName = "Nexus Agent", [switch]$StartupFolder, [switch]$Print)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Pyw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$Relay = Join-Path $Root "agent\nexus_relay.py"
if (-not (Test-Path $Pyw)) { throw "missing $Pyw - run scripts\setup_windows.bat first" }
New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null
if ($Print) { Write-Host "Task '$TaskName': `"$Pyw`" `"$Relay`" (cwd $Root) at logon of $env:USERNAME"; exit 0 }

if ($StartupFolder) {
    $lnk = Join-Path ([Environment]::GetFolderPath("Startup")) "$TaskName.lnk"
    $sh = New-Object -ComObject WScript.Shell
    $s = $sh.CreateShortcut($lnk)
    $s.TargetPath = $Pyw; $s.Arguments = "`"$Relay`""; $s.WorkingDirectory = $Root; $s.WindowStyle = 7
    $s.Save()
    Write-Host "-> created $lnk (runs at next logon)"
    exit 0
}

$action = New-ScheduledTaskAction -Execute $Pyw -Argument "`"$Relay`"" -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Nexus relay ($Root)" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "-> registered and started scheduled task '$TaskName'"
Write-Host "   check: Invoke-RestMethod http://127.0.0.1:8712/health   remove: scripts\uninstall_daemon_windows.ps1"
