@echo off
setlocal enabledelayedexpansion

:: RedClaw Windows Installer
:: Run this script from the project root (where dist/redclaw.exe lives)

set "INSTALL_DIR=%LOCALAPPDATA%\RedClaw"
set "EXE_SOURCE=dist\redclaw.exe"
set "VERSION=0.2.0"

echo.
echo  RedClaw v%VERSION% - Windows Installer
echo  ======================================
echo.

:: Check exe exists
if not exist "%EXE_SOURCE%" (
    echo  ERROR: %EXE_SOURCE% not found.
    echo  Run "python build.py" first to create the exe.
    exit /b 1
)

:: Create install directory
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"

:: Copy exe
echo  Installing to %INSTALL_DIR% ...
copy /Y "%EXE_SOURCE%" "%INSTALL_DIR%\redclaw.exe" >nul
if errorlevel 1 (
    echo  ERROR: Failed to copy exe.
    exit /b 1
)

:: Add to PATH (user-level, persistent)
echo  Adding to PATH ...
set "PATH_KEY=HKCU\Environment"
for /f "tokens=2*" %%A in ('reg query "%PATH_KEY%" /v Path 2^>nul') do set "USER_PATH=%%B"
echo "%USER_PATH%" | find /i "%INSTALL_DIR%" >nul
if errorlevel 1 (
    reg add "%PATH_KEY%" /v Path /t REG_EXPAND_SZ /d "%USER_PATH%;%INSTALL_DIR%" /f >nul 2>&1
    set "REFRESH_PATH=1"
)

:: Create config directory
if not exist "%USERPROFILE%\.redclaw" mkdir "%USERPROFILE%\.redclaw"

:: Create start menu shortcut
set "SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\RedClaw.lnk"
powershell -NoProfile -Command ^
    "$ws = New-Object -ComObject WScript.Shell; ^
     $sc = $ws.CreateShortcut('%SHORTCUT%'); ^
     $sc.TargetPath = '%INSTALL_DIR%\redclaw.exe'; ^
     $sc.WorkingDirectory = '%USERPROFILE%'; ^
     $sc.Description = 'RedClaw AI Agent'; ^
     $sc.Save()" 2>nul

echo.
echo  Done! RedClaw installed to:
echo    %INSTALL_DIR%\redclaw.exe
echo.
if defined REFRESH_PATH (
    echo  NOTE: PATH updated. Open a new terminal for changes to take effect.
) else (
    echo  Already on PATH.
)
echo.
echo  Quick start:
echo    redclaw                          - CLI REPL
echo    redclaw --mode dashboard         - Web dashboard
echo    redclaw --mode webchat           - Browser chat
echo    redclaw --mode telegram          - Telegram bot
echo.
pause
