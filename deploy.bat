@echo off
REM Triply deploy launcher: double-click to run deploy.ps1
REM (PowerShell scripts cannot execute by double-click, so this .bat bridges it)
setlocal
set "SCRIPT_DIR=%~dp0"
set "PS1=%SCRIPT_DIR%deploy.ps1"

if not exist "%PS1%" (
    echo [ERR] Cannot find %PS1%
    pause
    exit /b 1
)

echo ==================================================
echo  Triply one-click deploy (launched by double-click)
echo  Tip: pass args like "deploy -Build" or "deploy -FrontendOnly"
echo ==================================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*

set RC=%ERRORLEVEL%
echo.
if %RC%==0 (
    echo [DONE] Deploy script exited with code 0
) else (
    echo [FAIL] Deploy script exited with code %RC%
)
pause
endlocal
