@echo off
setlocal enabledelayedexpansion

:: RedClaw Windows Uninstaller

set "INSTALL_DIR=%LOCALAPPDATA%\RedClaw"

echo.
echo  RedClaw - Windows Uninstaller
echo  ==============================
echo.

if not exist "%INSTALL_DIR%\redclaw.exe" (
    echo  RedClaw is not installed.
    exit /b 0
)

:: Remove from PATH
set "PATH_KEY=HKCU\Environment"
for /f "tokens=2*" %%A in ('reg query "%PATH_KEY%" /v Path 2^>nul') do set "USER_PATH=%%B"
set "CLEANED_PATH="
for %%P in ("%USER_PATH:;=";"%") do (
    echo %%~P | find /i "%INSTALL_DIR%" >nul
    if errorlevel 1 (
        if defined CLEANED_PATH (
            set "CLEANED_PATH=!CLEANED_PATH!;%%~P"
        ) else (
            set "CLEANED_PATH=%%~P"
        )
    )
)
if defined CLEANED_PATH (
    reg add "%PATH_KEY%" /v Path /t REG_EXPAND_SZ /d "!CLEANED_PATH!" /f >nul 2>&1
)

:: Remove shortcut
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\RedClaw.lnk" 2>nul

:: Remove install directory
echo  Removing %INSTALL_DIR% ...
rd /s /q "%INSTALL_DIR%" 2>nul

echo.
echo  RedClaw uninstalled.
echo  Config and data in %USERPROFILE%\.redclaw\ were kept.
echo.
pause
