@echo off
rem ENCOMM SYSTEM WATCH - convenience wrapper for Start-SystemWatch.ps1
rem Usage: Start-SystemWatch.bat [-NoBrowser]
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-SystemWatch.ps1" %*
exit /b %ERRORLEVEL%