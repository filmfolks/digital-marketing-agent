# Remove the Nexus auto-start task / Startup shortcut (UNTESTED on real Windows).
param([string]$TaskName = "Nexus Agent")
$ErrorActionPreference = "Continue"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "-> removed scheduled task '$TaskName'"
} else { Write-Host "-> no scheduled task '$TaskName'" }
$lnk = Join-Path ([Environment]::GetFolderPath("Startup")) "$TaskName.lnk"
if (Test-Path $lnk) { Remove-Item $lnk; Write-Host "-> removed $lnk" }
Write-Host "A relay that is running now keeps running until you log off (or end pythonw.exe in Task Manager)."
