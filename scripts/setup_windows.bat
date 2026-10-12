@echo off
REM Nexus - Windows setup (double-click or run from cmd). Delegates to setup_windows.ps1.
REM Recreates .venv on THIS machine; always run after copying Nexus from a pendrive.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1" %*
if errorlevel 1 pause
