@echo off
setlocal
cd /d "%~dp0"

where pwsh.exe >nul 2>nul
if %errorlevel% equ 0 (
  pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\deploy-production.ps1" %*
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\deploy-production.ps1" %*
)

set "deploy_exit=%errorlevel%"
echo.
if not "%deploy_exit%"=="0" (
  echo Update failed. Review the error above; the server was not left on an unverified release.
) else (
  echo Update completed successfully.
)
pause
exit /b %deploy_exit%
