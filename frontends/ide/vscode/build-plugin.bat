@echo off
setlocal

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-plugin.ps1" %*
exit /b %ERRORLEVEL%
