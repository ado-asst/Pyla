@echo off
REM ====================================================================
REM  adb_connect.bat - Conecta a un dispositivo Android ya emparejado
REM  por ADB inalambrico (despues de haber usado adb_pair.bat).
REM
REM  USO:
REM     adb_connect.bat IP [PUERTO]      REM PUERTO por defecto = 5555
REM
REM  Ejemplo:
REM     adb_connect.bat 192.168.1.50
REM     adb_connect.bat 192.168.1.50 5555
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
    exit /b 1
)

echo === Conexion ADB Inalambrica ===
echo.

REM --- Validar argumentos ---
if "%~1"=="" (
    echo Uso: %0 ^<IP^> [PUERTO]
    echo   PUERTO por defecto = 5555
    echo.
    echo Ejemplo:
    echo   %0 192.168.1.50
    echo   %0 192.168.1.50 5555
    exit /b 1
)

set "IP=%~1"
if "%~2"=="" (
    set "PORT=5555"
) else (
    set "PORT=%~2"
)

set "ADDRESS=%IP%:%PORT%"

echo ^>^>^> Conectando a %ADDRESS%...
echo.

REM Asegurar servidor ADB
"%ADB%" start-server >nul 2>nul
timeout /t 1 /nobreak >nul

REM Desconectar primero cualquier conexion previa a esa IP (ignora errores)
"%ADB%" disconnect %ADDRESS% >nul 2>nul

REM Intentar conectar con reintentos
set "CONNECTED=0"
for /L %%A in (1,1,3) do (
    if !CONNECTED!==0 (
        "%ADB%" connect %ADDRESS% | findstr /R /I "connected to.*$ already connected.*$"
        if not errorlevel 1 (
            set "CONNECTED=1"
        ) else (
            echo Reintento %%A...
            timeout /t 2 /nobreak >nul
        )
    )
)

if not "%CONNECTED%"=="1" (
    echo.
    echo === No se pudo conectar a %ADDRESS% ===
    echo.
    echo Posibles causas:
    echo   - Aun no has emparejado el dispositivo. Ejecuta primero:
    echo       adb_pair.bat %IP% ^<PUERTO_PAREJA^> ^<CODIGO_8^>
    echo   - El puerto 5555 no es correcto. Mira la pantalla principal
    echo     'Depuracion inalambrica' en el movil para ver IP:PUERTO.
    echo   - El movil esta en otra red o durmiendo.
    exit /b 2
)

echo.
echo === Conexion establecida! ===
echo.
echo Dispositivos ADB actualmente conectados:
"%ADB%" devices -l
echo.

REM Verificar estado del dispositivo
"%ADB%" -s %ADDRESS% get-state >nul 2>nul
if errorlevel 1 (
    echo [AVISO] No se pudo verificar el estado del dispositivo.
    echo   Si dice 'unauthorized', mira el movil y acepta el dialogo de
    echo   'Permitir depuracion USB desde esta IP?'. Marca 'Permitir siempre'.
) else (
    for /f "delims=" %%S in ('"%ADB%" -s %ADDRESS% get-state 2^>nul') do set "STATE=%%S"
    if "!STATE!"=="device" (
        echo Estado del dispositivo: device ^(autorizado^)
    ) else (
        echo [AVISO] El dispositivo esta en estado '!STATE!'.
        echo   Si dice 'unauthorized', mira el movil y acepta el dialogo de
        echo   'Permitir depuracion USB desde esta IP?'. Marca 'Permitir siempre'.
    )
)

echo.
echo ^>^>^> Sugerencia:
echo Para que PylaAI use este dispositivo automaticamente, edita:
echo   cfg\general_config.toml
echo y pon:
echo   device_address = "%ADDRESS%"
echo.
echo Luego lanza el bot:
echo   python main.py
endlocal
