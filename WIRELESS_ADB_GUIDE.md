# Guía: jugar por ADB inalámbrico (código de 8 dígitos)

PylaAI ahora soporta **ADB inalámbrico** (Android 11+) para jugar en un
dispositivo Android **real** conectado por WiFi, sin cable USB y sin emulador.

Esta es una guía paso a paso para Windows 10/11.

---

## 1. Requisitos previos

1. **Android 11 o superior** en el móvil (necesario para "Depuración inalámbrica").
2. **ADB instalado en tu PC**. Si ya tienes el SDK de Android o cualquier emulador
   instalado, ya tienes `adb.exe`. Si no, descarga
   [Platform-Tools](https://developer.android.com/tools/releases/platform-tools)
   y descomprime, por ejemplo, en `C:\Android\platform-tools`. Añade esa carpeta
   al `PATH` o copia los archivos `adb_pair.bat`, `adb_connect.bat`,
   `adb_status.bat` dentro de esa carpeta.
3. **El PC y el móvil en la misma red WiFi** (o con ruteo entre ellos).
4. **PylaAI instalado** según el README principal (`python setup.py install`).

---

## 2. Activar Depuración inalámbrica en el móvil

1. Entra en **Ajustes → Acerca del teléfono** y toca 7 veces "Número de compilación"
   para activar las opciones de desarrollador.
2. Entra en **Ajustes → Sistema → Opciones de desarrollador → Depuración inalámbrica**
   y actívala.
3. Acepta el permiso que aparece en pantalla.

---

## 3. Emparejar el móvil (una sola vez por PC)

En el móvil, dentro de **Depuración inalámbrica**, toca
**"Emparejar dispositivo con código de emparejamiento"**. Verás algo así:

```
Dirección IP y puerto: 192.168.1.50:43251
Código de emparejamiento: 48293615
```

> ⚠️ El código de 8 dígitos **caduca a los ~60 segundos**. Ten la ventana de
> comandos del PC lista antes de generar el código en el móvil.

En tu PC, en la carpeta del proyecto PylaAI, ejecuta:

```bat
adb_pair.bat 192.168.1.50 43251 48293615
```

(Sustituye por la IP, puerto de emparejamiento y código que muestra tu móvil.)

Si todo va bien, verás `=== ¡Emparejamiento exitoso! ===`.

---

## 4. Conectar al móvil (cada vez que quieras usar el bot)

Una vez emparejado, sal de la pantalla "Emparejar dispositivo" y vuelve a la
**principal** de "Depuración inalámbrica". Ahí verás una línea como:

```
192.168.1.50:5555
```

> ⚠️ El puerto de **conexión** NO es el mismo que el de **emparejamiento**.
> El de conexión suele ser `5555` o similar.

En el PC, ejecuta:

```bat
adb_connect.bat 192.168.1.50 5555
```

La **primera vez** aparecerá un diálogo en el móvil pidiendo autorización.
**Mira la pantalla del móvil**, marca "Permitir siempre desde este equipo" y
acepta. Si no lo haces, el dispositivo quedará como `unauthorized` y el bot no
podrá controlarlo.

Para verificar el estado en cualquier momento:

```bat
adb_status.bat
```

Deberías ver algo como:

```
List of devices attached
192.168.1.50:5555    device product:... model:... transport_id:1
```

---

## 5. Configurar PylaAI para que use el móvil

Edita `cfg\general_config.toml` con cualquier editor (Bloc de notas, VSCode…)
y pon la IP:PUERTO del dispositivo en el campo `device_address`:

```toml
# === ADB Inalámbrico (Android 11+) ===
device_address = "192.168.1.50:5555"
```

Si `device_address` queda vacío `""`, PylaAI vuelve al comportamiento original
(escanea emuladores locales en 127.0.0.1).

---

## 6. Lanzar el bot

```bat
python main.py
```

PylaAI detectará automáticamente `device_address`, se conectará al móvil por
ADB y comenzará a jugar. Verás en la consola algo como:

```
[wireless] Conectando a dispositivo ADB remoto: 192.168.1.50:5555
[wireless] Conectado a 192.168.1.50:5555 -> 192.168.1.50:5555
[wireless] Dispositivo listo: 192.168.1.50:5555
Connected to device: 192.168.1.50:5555
[wireless] Dispositivo remoto detectado (192.168.1.50:5555).
[wireless] Despertando y forzando orientacion landscape...
Scrcpy client started successfully.
```

---

## 7. Resolución, orientación y rendimiento

El bot fue diseñado para 1920x1080 (emulador landscape). En un móvil físico,
estos son los puntos clave que PylaAI **gestiona automáticamente por ti**:

### Orientación landscape (forzada)
- El bot envía comandos ADB para:
  - `settings put system accelerometer_rotation 0` → desactiva auto-rotación
  - `settings put system user_rotation 1` → fuerza landscape (90°)
- Además, scrcpy recibe `lock_video_orientation=1` para que el stream de
  video SIEMPRE llegue en landscape aunque gires el móvil.
- Si quieres anular esto (no recomendado para Brawl Stars), edita
  `cfg/general_config.toml` y pon `scrcpy_lock_video_orientation = 0`.

### Mantener pantalla despierta
- El bot activa `stay_awake=true` en scrcpy → el móvil no se apaga mientras
  el bot está corriendo.
- Además, al iniciar envía `KEYCODE_WAKEUP` y un swipe para desbloquear la
  pantalla si estaba apagada.

### Resolución distinta de 1920x1080
- Si tu móvil tiene resolución nativa distinta (1080x2400, 1440x3200, etc.),
  el bot escala automáticamente las coordenadas de los botones usando
  `width_ratio` y `height_ratio`. **Debería funcionar correctamente**.
- scrcpy limita el ancho del frame a `scrcpy_max_width = 1920` por defecto
  para no saturar la CPU del PC con frames 4K.
- Verás un aviso `WARNING: Unexpected resolution: 1080x2400...` — es solo
  informativo, no es un error.

### Ajustes de rendimiento (solo si hay lag)
En `cfg/general_config.toml` puedes tunear scrcpy:

```toml
scrcpy_max_width = 1280        # reduce resolución del frame si tu PC va lento
scrcpy_bitrate = 2000000       # 2 Mbps, reduce ancho de banda
scrcpy_max_fps = 30            # limita a 30 FPS para reducir carga de CPU
scrcpy_stay_awake = true       # mantener siempre true
scrcpy_lock_video_orientation = 1  # siempre 1 (landscape) para Brawl Stars
```

---

## 8. Scripts disponibles (carpeta del proyecto)

| Script            | Función                                                      |
|-------------------|--------------------------------------------------------------|
| `adb_pair.bat`    | Empareja un móvil nuevo usando el código de 8 dígitos        |
| `adb_connect.bat` | Conecta al dispositivo (ya emparejado) por ADB inalámbrico   |
| `adb_status.bat`  | Muestra estado de ADB, dispositivos y configuración actual  |

---

## 9. Solución de problemas

### El emparejamiento falla con "cannot connect"
- Asegúrate de que **el móvil y el PC están en la misma red WiFi**.
- Comprueba que el firewall de Windows no bloquea `adb.exe`.
- En el móvil, prueba desactivar/activar "Depuración inalámbrica".

### `adb connect` dice `failed to connect`
- Tras emparejar, **el puerto de conexión NO es el de emparejamiento**.
- El puerto de conexión es el que aparece en la pantalla **principal**
  "Depuración inalámbrica" (típicamente `:5555` o similar).

### El dispositivo aparece como `unauthorized`
- Mira la pantalla del móvil: aparecerá un diálogo pidiendo autorización.
- Marca "Permitir siempre desde este equipo" y acepta.
- Si no aparece el diálogo, ejecuta `adb_connect.bat` de nuevo.

### `device offline` o se desconecta a los minutos
- El móvil puede estar ahorrando batería. Ajustes → Batería → desactiva
  optimización para "Servicios de Google Play" y para la app "Ajustes".
- Mantén la pantalla del móvil con WiFi siempre activo
  (Ajustes → WiFi → avanzado → "Mantener WiFi activo durante suspensión").

### PylaAI no detecta el dispositivo
- Ejecuta `adb_status.bat` para ver qué ve ADB.
- Verifica que `cfg\general_config.toml` tiene
  `device_address = "192.168.1.50:5555"` (con tu IP y puerto reales).
- Reinicia el servidor ADB: `adb kill-server && adb start-server`.

### El bot dice "No ADB devices came online after scan"
- Significa que no encuentra el dispositivo ni por emparejamiento remoto
  ni por escaneo local.
- Verifica que hiciste `adb_connect.bat` Y que el dispositivo aparece como
  `device` (no `offline` ni `unauthorized`) al ejecutar `adb_status.bat`.

### No recibes frames del scrcpy
- En `cfg\debug_settings.toml` activa `verbose_debug = true` para más detalle.
- Cierra otras apps que estén usando ADB al mismo tiempo.

### Los botones no se presionan donde deberían (offset de coordenadas)
- Esto suele pasar si Brawl Stars NO está en orientación landscape.
- Verifica en el móvil que Brawl Stars esté en modo horizontal.
- Comprueba que `cfg/general_config.toml` tenga
  `scrcpy_lock_video_orientation = 1`.
- Si tu móvil tiene notch/cámara perforada, Brawl Stars puede renderizar con
  bandas negras — el bot escala por la resolución REAL del frame, no la
  visible. Esto no debería causar problemas.

### El bot dice que la pantalla está en portrait (1080x1920)
- El bot fuerza landscape automáticamente, pero si tu versión de Android
  bloquea el comando `settings put system user_rotation`, puede fallar.
- Solución manual: en el móvil activa "Auto-rotación" y gira el móvil
  físicamente a horizontal. El bot funcionará igual.

---

## 10. Notas de seguridad

- El emparejamiento ADB da acceso total al móvil desde el PC: cuando no lo
  uses, **desactiva "Depuración inalámbrica"** en el móvil.
- Si vas a usar el bot en una red pública, recuerda que cualquier dispositivo
  en esa red podría intentar emparejarse con tu móvil (necesitarían el código
  de 8 dígitos, así que es seguro, pero conviene tenerlo en cuenta).
- Brawl Stars no detecta oficialmente el uso de bots externos, pero usar bots
  puede violar los Términos de Servicio de Supercell. Úsalo bajo tu
  responsabilidad.
