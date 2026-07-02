@echo off
REM ====================================================================
REM  adb_pair.bat - Empareja un dispositivo Android (11+) con ADB
REM  inalambrico usando el codigo de 8 digitos.
REM
REM  USO:
REM     adb_pair.bat IP PUERTO_PAREJA CODIGO_8_DIGITOS
REM
REM  Ejemplo:
REM     adb_pair.bat 192.168.1.50 43251 48293615
REM
REM  En el dispositivo Android (Opciones de desarrollador -> Depuracion inalambrica):
REM     1. Activa "Depuracion inalambrica"
REM     2. Pulsa "Emparejar dispositivo con codigo de emparejamiento"
REM     3. Anota IP:PUERTO y el codigo de 8 digitos
REM     4. Ejecuta este script con esos datos
REM ====================================================================

setlocal EnableDelayedExpansion

REM --- Localiza adb ---
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
    echo Instala Android Platform-Tools o agrega adb al PATH.
    echo Descarga: https://developer.android.com/tools/releases/platform-tools
    exit /b 1
)

echo === Emparejamiento ADB Inalambrico (codigo de 8 digitos) ===
echo ADB: %ADB%
"%ADB%" version | findstr /R "Android Debug Bridge"
echo.

REM --- Validar argumentos ---
if "%~3"=="" (
    echo Uso: %0 ^<IP^> ^<PUERTO_PAREJA^> ^<CODIGO_8_DIGITOS^>
    echo.
    echo Ejemplo:
    echo   %0 192.168.1.50 43251 48293615
    echo.
    echo Pasos en el movil (Android 11+^):
    echo   1. Ajustes -^> Opciones de desarrollador -^> Depuracion inalambrica
    echo   2. Activa la opcion
    echo   3. Toca 'Emparejar dispositivo con codigo de emparejamiento'
    echo   4. Veras IP:PUERTO y un codigo de 8 digitos
    exit /b 1
)

set "IP=%~1"
set "PAIR_PORT=%~2"
set "CODE=%~3"

REM Validar codigo (6-8 digitos)
echo %CODE%| findstr /R "^[0-9][0-9][0-9][0-9][0-9][0-9][0-9]*[0-9]*$" >nul
if errorlevel 1 (
    echo [ERROR] El codigo debe ser 6-8 digitos numericos. Recibido: '%CODE%'
    exit /b 1
)

echo ^>^>^> Emparejando con %IP%:%PAIR_PORT% usando codigo %CODE%...
echo.

REM Asegurar que el servidor ADB este corriendo
"%ADB%" start-server >nul 2>nul
timeout /t 1 /nobreak >nul

REM Ejecutar adb pair
"%ADB%" pair %IP%:%PAIR_PORT% %CODE%
if errorlevel 1 (
    echo.
    echo === Fallo el emparejamiento ===
    echo.
    echo Posibles causas:
    echo   - El codigo de 8 digitos expiro (solo dura ~60s, vuelve a generar uno nuevo^)
    echo   - IP o puerto incorrectos (debes usar el puerto de la pantalla 'Emparejar dispositivo'^)
    echo   - El movil y el PC no estan en la misma red WiFi
    echo   - Firewall del PC bloqueando la conexion saliente
    exit /b 2
)

echo.
echo === Emparejamiento exitoso! ===
echo.
echo Ahora necesitas CONECTAR al dispositivo.
echo El puerto de CONEXION es diferente al puerto de EMPAREJAMIENTO.
echo.
echo En el movil, en la pantalla principal de 'Depuracion inalambrica',
echo veras una linea como:
echo   192.168.1.50:5555
echo.
echo Ejecuta entonces:
echo   adb_connect.bat %IP% 5555
echo.
echo O si quieres que PylaAI se conecte automaticamente en cada inicio,
echo edita cfg\general_config.toml y pon:
echo   device_address = "%IP%:5555"
echo.
echo Luego lanza el bot:
echo   python main.py
endlocal
