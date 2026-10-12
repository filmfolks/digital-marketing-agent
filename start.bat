@echo off
REM Nexus launcher (Windows cmd) - delegates to start.ps1.  Usage: start.bat [-Lan] [-Check]
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
