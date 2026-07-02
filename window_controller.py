import atexit
import math
from concurrent.futures import ThreadPoolExecutor
import threading
import time

# === Plan A: usar scrcpy_native (cliente minimalista propio) en lugar de scrcpy-client 0.4.7 ===
# scrcpy-client 0.4.7 falla al conectar el socket localabstract en algunos moviles fisicos
# (Samsung S21 FE Android 13, etc.). scrcpy_native usa el mismo scrcpy-server v1.25 pero con
# una implementacion de cliente mas robusta (adb forward + PyAV decoder directo).
import scrcpy_native as scrcpy
from adbutils import adb, AdbDevice
from debug_view import DebugViewPublisher
from utils import config_bool, load_toml_as_dict, save_dict_as_toml, invalidate_toml_cache

brawl_stars_width, brawl_stars_height = 1920, 1080

press_coords_dict = {
    "hypercharge": (1400, 990),
    "gadget": (1640, 990),
    "attack": (1725, 800),
    "proceed": (1660, 980),
    "middle_got_it": (960, 980),
    "super": (1510, 880),
    "play_again": (1360, 920),
    "continue_or_equip": (700, 1000),
}
KNOWN_BS_PACKAGES = ("com.supercell.brawlstars", "bsd.suitcase.release")

import random as _random


def _scrcpy_options_from_config() -> dict:
    """
    Lee opciones de scrcpy desde cfg/general_config.toml.
    Devuelve un dict con: max_width, bitrate, max_fps, stay_awake, lock_video_orientation.
    Por defecto optimizado para movil fisico por ADB inalambrico.
    """
    cfg = load_toml_as_dict("cfg/general_config.toml")
    return {
        "max_width": int(cfg.get("scrcpy_max_width", 1024)),
        "bitrate": int(cfg.get("scrcpy_bitrate", 1500000)),
        "max_fps": int(cfg.get("scrcpy_max_fps", 30)),
        "stay_awake": config_bool(cfg.get("scrcpy_stay_awake", True), True),
        # 0=unlocked, 1=landscape, 2=portrait. Por defecto landscape (1) para Brawl Stars.
        "lock_video_orientation": int(cfg.get("scrcpy_lock_video_orientation", 1)),
    }


def _create_scrcpy_client(device, max_ips="auto") -> "scrcpy.ScrcpyClient":
    """
    Crea un cliente scrcpy con opciones optimizadas para el dispositivo.
    - max_ips puede ser "auto" (sin limite de FPS) o un entero (FPS maximo).
    - Usa stay_awake=True para evitar que el movil se duerma.
    - max_width=1920 para limitar ancho de banda en moviles 2K/4K.
    - Plan A: usa scrcpy_native.ScrcpyClient (no scrcpy.Client).
    """
    opts = _scrcpy_options_from_config()
    common_kwargs = dict(
        device=device,
        max_width=opts["max_width"],
        bitrate=opts["bitrate"],
        stay_awake=opts["stay_awake"],
        lock_screen_orientation=opts["lock_video_orientation"],
    )
    # max_fps: prioriza argumento explicito, luego config
    fps_to_use = None
    if max_ips not in ("auto", None):
        try:
            fps_to_use = int(max_ips)
        except (TypeError, ValueError):
            fps_to_use = None
    if (fps_to_use is None or fps_to_use <= 0) and opts["max_fps"] > 0:
        fps_to_use = opts["max_fps"]
    if fps_to_use is not None and fps_to_use > 0:
        common_kwargs["max_fps"] = fps_to_use
    return scrcpy.ScrcpyClient(**common_kwargs)


def _unlock_device(device) -> None:
    """
    Despierta y desbloquea el movil (si esta apagado/bloqueado).
    Solo aplica a moviles fisicos; en emuladores no hace nada malo.
    """
    try:
        # KEYCODE_WAKEUP = 224
        device.shell("input keyevent 224")
        time.sleep(0.5)
        # Desliza hacia arriba para salir del bloqueo (si hay)
        device.shell("input swipe 500 1500 500 300 200")
        time.sleep(0.3)
    except Exception as e:
        print(f"[wireless] No se pudo despertar el dispositivo: {e}")


def _force_landscape(device) -> None:
    """
    Fuerza orientacion landscape en el dispositivo.
    Estrategia:
    1) settings put system accelerometer_rotation 0 (desactiva auto-rotacion)
    2) settings put system user_rotation 1 (fuerza rotacion landscape = 90 grados)
    3) Si la resolucion sigue en portrait, avisa al usuario (el lock_video_orientation
       del scrcpy server se encargara de entregar el frame rotado al bot).
    """
    try:
        # Desactiva auto-rotacion
        device.shell("settings put system accelerometer_rotation 0")
        time.sleep(0.2)
        # Fuerza rotacion landscape (1 = 90 grados, landscape)
        device.shell("settings put system user_rotation 1")
        time.sleep(0.5)

        # Verifica la resolucion actual
        size_out = device.shell("wm size").strip()
        # Ultima linea tipo "Override size: 464x1024" o "Physical size: 1080x2400"
        current_w, current_h = None, None
        for line in size_out.splitlines():
            line = line.strip()
            if "x" in line.lower() and ":" in line:
                try:
                    size_str = line.split(":", 1)[1].strip()
                    w_h = size_str.lower().split("x")
                    if len(w_h) == 2:
                        current_w, current_h = int(w_h[0]), int(w_h[1])
                except Exception:
                    continue

        if current_w and current_h:
            if current_h > current_w:
                print(
                    f"[wireless] Movil sigue en portrait ({current_w}x{current_h}). "
                    f"El scrcpy server rotara el frame automaticamente via "
                    f"lock_video_orientation=1. Asegurate de que Brawl Stars este "
                    f"abierto en horizontal."
                )
            else:
                print(f"[wireless] Orientacion landscape OK: {current_w}x{current_h}")
    except Exception as e:
        print(f"[wireless] No se pudo forzar orientacion landscape: {e}")


def _is_remote_device(serial: str) -> bool:
    """Devuelve True si el serial parece un dispositivo remoto (IP:PORT)."""
    if not serial:
        return False
    return ":" in serial and any(c.isdigit() for c in serial.split(":")[0])


def restart_adb_server() -> None:
    try:
        adb.server_kill()
    except Exception:
        pass
    time.sleep(0.5)
    try:
        adb.server_start()
    except Exception:
        pass
    time.sleep(0.5)


def online_devices():
    out = []
    for d in adb.device_list():
        try:
            state = d.get_state() if hasattr(d, "get_state") else d.state
        except Exception:
            state = "device"
        if state == "device":
            out.append(d)
    return out


def _connect_remote_device(address: str, verbose: bool = False):
    """
    Conecta a un dispositivo Android por ADB inalámbrico (Android 11+).
    address puede ser 'IP' o 'IP:PUERTO'. Si no hay puerto, usa 5555.
    Devuelve el AdbDevice o None si falla.
    """
    if ":" not in address:
        address_full = f"{address}:5555"
    else:
        address_full = address
    if verbose:
        print(f"[wireless] Intentando adb connect {address_full} ...")
    # Limpia conexiones previas a esa IP para evitar phantom devices
    try:
        adb.disconnect(address_full)
    except Exception:
        pass
    import time as _t
    for attempt in range(3):
        try:
            _r = adb.connect(address_full, timeout=10)
            # adbutils 1.x devuelve un mensaje tipo 'connected to IP:PORT' (str);
            # adbutils 2.x devuelve un AdbDevice directamente.
            if isinstance(_r, str):
                # El serial de un dispositivo remoto es exactamente IP:PORT
                dev = adb.device(serial=address_full)
            else:
                dev = _r
            if verbose:
                print(f"[wireless] Conectado a {address_full} -> {dev.serial}")
            # Verifica que esté realmente online (no offline/unauthorized)
            try:
                state = dev.get_state() if hasattr(dev, "get_state") else dev.state
            except Exception:
                state = "device"
            if state == "device":
                return dev
            if verbose:
                print(f"[wireless] Estado: {state}. Esperando autorizacion en el movil...")
            _t.sleep(2)
        except Exception as e:
            if verbose:
                print(f"[wireless] Intento {attempt+1} fallo: {e}")
            _t.sleep(1)
    return None


def discover_device(verbose: bool = False) -> AdbDevice:
    cfg = load_toml_as_dict("cfg/general_config.toml")
    preferred_port = cfg.get("emulator_port", 5037)
    device_address = cfg.get("device_address", "")  # ej: "192.168.1.50:5555"

    # === MODO ADB INALAMBRICO (Android 11+) ===
    # Si el usuario configuro device_address, intentamos conectar a ese dispositivo.
    if device_address and isinstance(device_address, str) and device_address.strip():
        addr = device_address.strip()
        # Si es IP publica/privada (no 127.x.x.x ni localhost), es un dispositivo remoto
        if not addr.startswith("127.") and not addr.startswith("localhost"):
            print(f"[wireless] Conectando a dispositivo ADB remoto: {addr}")
            dev = _connect_remote_device(addr, verbose=verbose)
            if dev is not None:
                print(f"[wireless] Dispositivo listo: {dev.serial}")
                return dev
            print(f"[wireless] No se pudo conectar a {addr}. Cayendo a escaneo local...")

    # === MODO EMULADOR LOCAL (comportamiento original) ===
    candidates = [5137, 5555, 16384, 7555, 5635, 62001, 62025, 62026, 7556, 7565, 16416] + list(range(5556, 5566)) + list(range(5565, 5756, 10))

    def _safe_connect(port: int):
        dev = adb.connect(f"127.0.0.1:{port}")
        return dev

    def _try(port):
        try:
            _safe_connect(port)
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=len(candidates)) as executor:
        executor.map(_try, candidates)

    devices = online_devices()
    if verbose:
        print(f"Online devices after scan: {[d.serial for d in devices]}")

    if not devices:
        raise ConnectionError(
            "No ADB devices came online after scan.\n"
            "Para ADB inalambrico (Android 11+):\n"
            "  1) Ejecuta adb_pair.bat IP PUERTO_PAREJA CODIGO_8\n"
            "  2) Ejecuta adb_connect.bat IP PUERTO\n"
            "  3) Configura 'device_address' en cfg/general_config.toml\n"
            "  4) Vuelve a lanzar main.py"
        )

    if preferred_port:
        pref = next((d for d in devices if d.serial.endswith(f"{preferred_port}")), None)
        if pref:
            if verbose and len(devices) > 1:
                print(f"Multiple devices online; using configured port {preferred_port} ({pref.serial})")
            return pref

    if len(devices) == 1:
        return devices[0]

    chosen = devices[0]
    print(f"Multiple ADB devices online and no port configured. "
          f"Picking {chosen.serial} (first one). Others: "
          f"{[d.serial for d in devices if d is not chosen]}")
    return chosen

class WindowController:
    def __init__(self, max_ips="auto"):
        self.scale_factor = None
        self.width = None
        self.height = None
        self.width_ratio = None
        self.height_ratio = None
        self.joystick_x, self.joystick_y = None, None
        self.BRAWL_STARS_PACKAGE = load_toml_as_dict("cfg/general_config.toml")["brawl_stars_package"]
        self.verbose_debug = config_bool(
            load_toml_as_dict("cfg/debug_settings.toml").get("verbose_debug"),
            False
        )
        print("Connecting to ADB (might take up to 2 minutes)...")
        try:
            self.device = discover_device(verbose=self.verbose_debug)
            print(f"Connected to device: {self.device.serial}")

            self.frame_lock = threading.Lock()
            self.max_ips = max_ips
            # Si es un dispositivo remoto (movil fisico por ADB inalambrico),
            # despertarlo y forzar orientacion landscape antes de iniciar scrcpy.
            if _is_remote_device(self.device.serial):
                print(f"[wireless] Dispositivo remoto detectado ({self.device.serial}).")
                print(f"[wireless] Despertando y forzando orientacion landscape...")
                _unlock_device(self.device)
                _force_landscape(self.device)
            self.scrcpy_client = _create_scrcpy_client(self.device, self.max_ips)
            self.last_frame = None
            self.last_frame_time = 0.0
            self.last_joystick_pos = (None, None)
            self.FRAME_STALE_TIMEOUT = 15.0
            self.re_apply_movement = config_bool(
                load_toml_as_dict("cfg/debug_settings.toml").get("re_apply_movement"),
                True
            )
            self.debug_view = DebugViewPublisher.from_config()

            def on_frame(frame):
                if frame is not None:
                    with self.frame_lock:
                        self.last_frame = frame
                        self.last_frame_time = time.time()

            self.scrcpy_client.add_listener(scrcpy.EVENT_FRAME, on_frame)
            self.scrcpy_client.start(threaded=True)
            atexit.register(self.close)
            print("Scrcpy client started successfully.")

        except Exception:
            raise Exception(f"Error during ADB/scrcpy initialization\nFailed to connect to the emulator/device.\nMake sure you have ADB enabled in your emulator settings. If you don't know how, check https://vimeo.com/1174882529?fl=pl&fe=s.\n if it still doesn't work, check https://discord.com/channels/1205263029269438574/1227618442073342002/1499331741838610433 to try fixing it.")
        self.are_we_moving = False
        self.PID_JOYSTICK = 1
        self.PID_ATTACK = 2

    def get_latest_frame(self):
        with self.frame_lock:
            if self.last_frame is None:
                return None, 0.0
            return self.last_frame, self.last_frame_time

    def force_rediscover(self) -> bool:
        print("Restarting ADB server and re-discovering device.")
        try:
            self.scrcpy_client.stop()
        except Exception:
            pass
        restart_adb_server()
        try:
            new_dev = discover_device(self.verbose_debug)
        except ConnectionError:
            return False
        self.device = new_dev
        print(f"Re-discovered device: {self.device.serial}")
        return True

    def reconnect_scrcpy(self, max_retries=3):
        for attempt in range(1, max_retries + 1):
            print(f"Scrcpy reconnect attempt {attempt}/{max_retries}")
            try:
                self.scrcpy_client.stop()
            except Exception:
                pass
            time.sleep(1)

            with self.frame_lock:
                self.last_frame = None
                self.last_frame_time = 0.0

            self.are_we_moving = False
            self.last_joystick_pos = (None, None)

            try:
                _ = self.device.get_state()
            except Exception:
                if not self.force_rediscover():
                    print("Device gone and re-discovery failed.")
                    time.sleep(2 * attempt)
                    continue

            def on_frame(frame):
                if frame is not None:
                    with self.frame_lock:
                        self.last_frame = frame
                        self.last_frame_time = time.time()

            try:
                # En reconexion tambien despertamos y forzamos landscape si es remoto
                if _is_remote_device(self.device.serial):
                    _unlock_device(self.device)
                    _force_landscape(self.device)
                self.scrcpy_client = _create_scrcpy_client(self.device, self.max_ips)
                self.scrcpy_client.add_listener(scrcpy.EVENT_FRAME, on_frame)
                self.scrcpy_client.start(threaded=True)
            except Exception as e:
                print(f"Scrcpy client creation failed: {e}")
                time.sleep(2 * attempt)
                continue

            deadline = time.time() + 8
            while time.time() < deadline:
                _, ft = self.get_latest_frame()
                if ft > 0 and (time.time() - ft) < 2:
                    print(f"Scrcpy feed restored on attempt {attempt}")
                    return True
                time.sleep(0.5)

            print(f"Attempt {attempt} did not restore frame feed")
            time.sleep(2 * attempt)

        print("All scrcpy reconnect attempts exhausted")
        return False

    def restart_brawl_stars(self):
        self.device.app_stop(self.BRAWL_STARS_PACKAGE)
        time.sleep(1)
        self.device.app_start(self.BRAWL_STARS_PACKAGE)
        time.sleep(3)
        print("Brawl stars restarted successfully.")

    def is_brawl_stars_running(self):
        try:
            opened_app = self.device.app_current().package.strip()
            detected_known_package = False
            for package in KNOWN_BS_PACKAGES:
                if opened_app == package:
                    detected_known_package = True
                    break
            if detected_known_package:
                if opened_app != self.BRAWL_STARS_PACKAGE:
                    general_config = load_toml_as_dict("cfg/general_config.toml")
                    general_config["brawl_stars_package"] = opened_app
                    save_dict_as_toml(general_config, "cfg/general_config.toml")
                    self.BRAWL_STARS_PACKAGE = opened_app
                    invalidate_toml_cache("cfg/general_config.toml")
                    print(f"Detected Brawl Stars running under the '{opened_app}' package. Updating configuration to match.")
            return opened_app == self.BRAWL_STARS_PACKAGE.strip()
        except Exception as e:
            print(f"Error checking if Brawl Stars is running: {e}")
            return False

    def screenshot(self):
        frame, frame_time = self.get_latest_frame()

        deadline = time.time() + 15
        while frame is None:
            if time.time() > deadline:
                raise ConnectionError(
                    "No frame received from scrcpy within 15s. "
                    "Check USB/emulator connection."
                )
            print("Waiting for first frame...")
            time.sleep(0.1)
            frame, frame_time = self.get_latest_frame()

        age = time.time() - frame_time
        if frame_time > 0 and age > self.FRAME_STALE_TIMEOUT:
            print(f"WARNING: scrcpy frame is {age:.1f}s stale -- feed may be frozen")

        if not self.width or not self.height:
            self.width = frame.shape[1]
            self.height = frame.shape[0]
            if (self.width, self.height) != (brawl_stars_width, brawl_stars_height):
                remote_note = ""
                if _is_remote_device(self.device.serial):
                    remote_note = (
                        "\n[wireless] Para movil fisico por ADB inalambrico:"
                        "\n  - Asegurate de que Brawl Stars este en modo LANDSCAPE"
                        " (giro automatico activado en el movil o forzado por el bot)."
                        "\n  - La resolucion nativa del movil no es 1920x1080, pero el bot"
                        " escala coordenadas automaticamente. Deberia funcionar igual."
                        "\n  - Si el boton esta mal posicionado, revisa que Brawl Stars"
                        " este en orientacion landscape (gira el movil)."
                    )
                print(
                    f"WARNING: Unexpected resolution: {self.width}x{self.height}. "
                    f"Expected {brawl_stars_width}x{brawl_stars_height}. "
                    f"Coordinadas seran escaladas automaticamente.{remote_note}"
                )
            self.width_ratio = self.width / brawl_stars_width
            self.height_ratio = self.height / brawl_stars_height
            self.joystick_x, self.joystick_y = 220 * self.width_ratio, 870 * self.height_ratio
            self.scale_factor = min(self.width_ratio, self.height_ratio)
        return frame

    def touch_down(self, x, y, pointer_id=0):
        try:
            self.scrcpy_client.control.touch(int(x), int(y), scrcpy.ACTION_DOWN, pointer_id)
        except Exception as e:
            print(f"Error during touch_down at ({x}, {y}) with pointer_id {pointer_id}: {e}")
            if self.reconnect_scrcpy() :
                try:
                    self.scrcpy_client.control.touch(int(x), int(y), scrcpy.ACTION_DOWN, pointer_id)
                except Exception as e2:
                    print(f"Retry after reconnect failed during touch_down at ({x}, {y}) with pointer_id {pointer_id}: {e2}")

    def touch_move(self, x, y, pointer_id=0):
        try:
            self.scrcpy_client.control.touch(int(x), int(y), scrcpy.ACTION_MOVE, pointer_id)
        except Exception as e:
            print(f"Error during touch_move at ({x}, {y}) with pointer_id {pointer_id}: {e}")
            if self.reconnect_scrcpy():
                try:
                    self.scrcpy_client.control.touch(int(x), int(y), scrcpy.ACTION_MOVE, pointer_id)
                except Exception as e2:
                    print(f"Retry after reconnect failed during touch_move at ({x}, {y}) with pointer_id {pointer_id}: {e2}")

    def touch_up(self, x, y, pointer_id=0):
        try:
            self.scrcpy_client.control.touch(int(x), int(y), scrcpy.ACTION_UP, pointer_id)
        except Exception as e:
            print(f"Error during touch_up at ({x}, {y}) with pointer_id {pointer_id}: {e}")
            if self.reconnect_scrcpy():
                try:
                    self.scrcpy_client.control.touch(int(x), int(y), scrcpy.ACTION_UP, pointer_id)
                except Exception as e2:
                    print(f"Retry after reconnect failed during touch_up at ({x}, {y}) with pointer_id {pointer_id}: {e2}")

    def move(self, x, y):
        target_x = self.joystick_x + x
        target_y = self.joystick_y + y
        if not self.are_we_moving:
            self.touch_down(self.joystick_x, self.joystick_y, pointer_id=self.PID_JOYSTICK)
            self.touch_move(target_x, target_y, pointer_id=self.PID_JOYSTICK)
            self.are_we_moving = True
            self.last_joystick_pos = (target_x, target_y)
            return

        if not self.re_apply_movement and self.last_joystick_pos == (target_x, target_y):
            return

        self.touch_move(target_x, target_y, pointer_id=self.PID_JOYSTICK)
        self.last_joystick_pos = (target_x, target_y)

    def release_movement(self):
        if self.are_we_moving:
            self.touch_up(self.joystick_x, self.joystick_y, pointer_id=self.PID_JOYSTICK)
            self.are_we_moving = False
            self.last_joystick_pos = (None, None)

    def click(self, x: int, y: int, delay=0.02, already_include_ratio=True, touch_up=True, touch_down=True):
        if not already_include_ratio:
            x = x * self.width_ratio
            y = y * self.height_ratio
        if touch_down: self.touch_down(x, y, pointer_id=self.PID_ATTACK)
        time.sleep(delay)
        if touch_up: self.touch_up(x, y, pointer_id=self.PID_ATTACK)

    def press(self, key, delay=0.02, touch_up=True, touch_down=True):
        if key not in press_coords_dict:
            return
        x, y = press_coords_dict[key]
        target_x = x * self.width_ratio
        target_y = y * self.height_ratio
        self.click(target_x, target_y, delay, touch_up=touch_up, touch_down=touch_down)

    def swipe(self, start_x, start_y, end_x, end_y, duration=0.2):
        dist_x = end_x - start_x
        dist_y = end_y - start_y
        distance = math.sqrt(dist_x ** 2 + dist_y ** 2)

        if distance == 0:
            return

        step_len = 25
        steps = max(int(distance / step_len), 1)
        step_delay = duration / steps

        self.touch_down(int(start_x), int(start_y), pointer_id=self.PID_ATTACK)
        for i in range(1, steps + 1):
            t = i / steps
            cx = start_x + dist_x * t
            cy = start_y + dist_y * t
            time.sleep(step_delay)
            self.touch_move(int(cx), int(cy), pointer_id=self.PID_ATTACK)
        self.touch_up(int(end_x), int(end_y), pointer_id=self.PID_ATTACK)

    def close(self):
        try:
            self.debug_view.close()
        except Exception as exc:
            print(f"Debug view close failed: {exc}")
        self.stop_scrcpy_with_timeout()

    def stop_scrcpy_with_timeout(self, timeout=2.0):
        def stop_client():
            try:
                self.scrcpy_client.stop()
            except Exception as exc:
                print(f"Scrcpy stop failed: {exc}")

        stop_thread = threading.Thread(target=stop_client, daemon=True, name="scrcpy-stop")
        stop_thread.start()
        stop_thread.join(timeout=timeout)
        if stop_thread.is_alive():
            print("Scrcpy stop is still running in the background; continuing shutdown.")
