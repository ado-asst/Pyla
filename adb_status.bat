@echo off
REM ====================================================================
REM  adb_status.bat - Muestra el estado actual de ADB y dispositivos.
REM  Util para diagnosticar si hay un dispositivo conectado.
REM ====================================================================

setlocal

set "ADB="
where adb >nul 2>nul && set "ADB=adb"
if not defined ADB (
    if exist "%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe" (
        set "ADB=%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"
    )
)
if not defined ADB (
    if exist "C:\Android\platform-tools\adb.exe" set "ADB=C:\Android\platform-tools\adb.exe"
)
if not defined ADB (
    if exist "%~dp0platform-tools\adb.exe" set "ADB=%~dp0platform-tools\adb.exe"
)
if not defined ADB (
    echo [ERROR] No se encontro 'adb.exe'.
    exit /b 1
)

echo === Estado de ADB ===
echo Binario: %ADB%
"%ADB%" version 2>nul | findstr /R "Android Debug Bridge"
echo.

echo ^>^>^> adb start-server
"%ADB%" start-server 2>nul
echo.

echo ^>^>^> adb devices -l
"%ADB%" devices -l
echo.

echo ^>^>^> device_address configurado en PylaAI:
if exist "%~dp0cfg\general_config.toml" (
    findstr /B "device_address" "%~dp0cfg\general_config.toml" 2>nul
    if errorlevel 1 echo (no configurado^)
) else (
    echo (no existe cfg\general_config.toml^)
)
echo.

echo === Fin del informe ===
endlocal
