"""
Minimal scrcpy client for PylaAI (Plan A).

Replaces the bundled scrcpy-client 0.4.7 which fails to connect on some
physical Android devices (Samsung S21 FE on Android 13, etc.).

This module is a drop-in replacement that exposes the same minimal API used by
window_controller.py:
    - EVENT_FRAME, EVENT_INIT
    - ACTION_DOWN, ACTION_UP, ACTION_MOVE
    - ScrcpyClient(device, max_width, bitrate, max_fps, stay_awake, lock_screen_orientation)
    - client.add_listener(event, callback)
    - client.start(threaded=True)
    - client.stop()
    - client.control.touch(x, y, action, pointer_id)

Protocol: scrcpy-server v1.25
    - Pushes scrcpy-server.jar to /data/local/tmp/scrcpy-server.jar
    - Starts server via `adb shell` with tunnel_forward=true
    - Forwards PC port 27183 -> device localabstract:scrcpy
    - Connects two sockets: video (first) and control (second)
    - Reads raw H.264 stream from video socket
    - Decodes with PyAV CodecContext (h264 decoder)
    - Sends INJECT_TOUCH_EVENT messages on control socket

Tested working on:
    - Samsung Galaxy S21 FE (SM-G781U1) Android 13
    - Should work on any Android 11+ device with wireless ADB
"""

import os
import socket
import struct
import subprocess
import threading
import time
from typing import Callable, List, Optional

try:
    import av
except ImportError:
    av = None

# === Event types (string identifiers, matching scrcpy-client 0.4.7) ===
EVENT_FRAME = "frame"
EVENT_INIT = "init"

# === Touch actions (match scrcpy MotionEvent constants) ===
ACTION_DOWN = 0
ACTION_UP = 1
ACTION_MOVE = 2

# === scrcpy server version we bundle ===
SCRCPY_VERSION = "1.25"

# === Paths ===
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_JAR_LOCAL = os.path.join(_BASE_DIR, "scrcpy", "scrcpy-server.jar")
SERVER_JAR_REMOTE = "/data/local/tmp/scrcpy-server.jar"

# PC port forwarded to device's abstract socket "scrcpy"
LOCAL_PORT = 27183

# Control message type for touch injection (scrcpy 1.x)
TYPE_INJECT_TOUCH_EVENT = 0

# Receive chunk size for video socket reads
RECV_CHUNK = 65536


def _find_adb_path() -> str:
    """Locate the adb executable, preferring adbutils' bundled one."""
    # 1) Intenta via adbutils (mas confiable)
    try:
        from adbutils._utils import get_adb_path
        p = get_adb_path()
        if p and os.path.exists(p):
            return p
    except Exception:
        pass
    # 2) Busca en PATH con extension .exe en Windows
    import shutil
    exe_name = "adb.exe" if os.name == "nt" else "adb"
    found = shutil.which("adb") or shutil.which(exe_name)
    if found:
        return found
    # 3) Busca en ubicaciones comunes de Windows
    if os.name == "nt":
        candidate_paths = [
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk", "platform-tools", "adb.exe"),
            os.path.join(os.environ.get("USERPROFILE", ""), "AppData", "Local", "Android", "Sdk", "platform-tools", "adb.exe"),
            r"C:\Android\platform-tools\adb.exe",
            r"C:\Program Files\Android\Android Studio\bin\adb.exe",
            r"C:\Program Files (x86)\Android\android-sdk\platform-tools\adb.exe",
        ]
        for c in candidate_paths:
            if c and os.path.exists(c):
                return c
    # 4) Ultimo recurso: el nombre solo (puede fallar en Windows)
    return exe_name


def _recv_exactly(sock: socket.socket, n: int, stop_event: threading.Event) -> bytes:
    """Receive exactly n bytes from socket. Raises ConnectionError on EOF."""
    buf = bytearray()
    while len(buf) < n:
        if stop_event.is_set():
            raise ConnectionError("recv aborted (stop event set)")
        try:
            chunk = sock.recv(min(n - len(buf), RECV_CHUNK))
        except socket.timeout as e:
            continue
        except OSError as e:
            raise ConnectionError(f"socket error during recv: {e}")
        if not chunk:
            raise ConnectionError(
                f"socket closed during recv (got {len(buf)}/{n} bytes)"
            )
        buf.extend(chunk)
    return bytes(buf)


class _ControlProtocol:
    """Sends control messages (touch events) to scrcpy server."""

    def __init__(self, control_socket: socket.socket, width: int, height: int):
        self.socket = control_socket
        self.width = width
        self.height = height
        self._lock = threading.Lock()
        self._closed = False

    def touch(
        self,
        x: int,
        y: int,
        action: int,
        pointer_id: int = 0,
        pressure: float = 1.0,
    ) -> None:
        """
        Send a touch event.

        action: ACTION_DOWN, ACTION_UP, or ACTION_MOVE
        pointer_id: unique per "finger". Use different IDs for simultaneous touches.
        pressure: 0.0 (released) to 1.0 (max). 0xFFFF in scrcpy 1.x uint16 format.
        """
        if self._closed:
            raise ConnectionError("control socket already closed")

        # scrcpy 1.x INJECT_TOUCH_EVENT message format (24 bytes total):
        #   byte 0:     type (uint8)  = TYPE_INJECT_TOUCH_EVENT (0)
        #   byte 1:     action (uint8) = ACTION_DOWN/UP/MOVE
        #   bytes 2-9:  pointer_id (uint64 BE)
        #   bytes 10-13: x (int32 BE, signed)
        #   bytes 14-17: y (int32 BE, signed)
        #   bytes 18-19: screen_width (uint16 BE)
        #   bytes 20-21: screen_height (uint16 BE)
        #   bytes 22-23: pressure (uint16 BE, 0xFFFF = max)
        pressure_uint16 = 0xFFFF if pressure > 0.0 else 0
        msg = struct.pack(
            ">BBQiiHHH",
            TYPE_INJECT_TOUCH_EVENT,
            action,
            int(pointer_id) & 0xFFFFFFFFFFFFFFFF,
            int(x),
            int(y),
            int(self.width),
            int(self.height),
            pressure_uint16,
        )
        with self._lock:
            try:
                self.socket.sendall(msg)
            except Exception as e:
                raise ConnectionError(f"failed to send touch event: {e}")

    def close(self) -> None:
        self._closed = True
        with self._lock:
            try:
                self.socket.close()
            except Exception:
                pass


class ScrcpyClient:
    """
    Minimal scrcpy client. Drop-in replacement for scrcpy.Client (subset of API).

    Lifecycle:
        client = ScrcpyClient(device, max_width=1024, ...)
        client.add_listener(EVENT_FRAME, on_frame_callback)
        client.start(threaded=True)
        # ... frames arrive via callback ...
        client.stop()
    """

    def __init__(
        self,
        device,
        max_width: int = 1920,
        bitrate: int = 4000000,
        max_fps: int = 0,
        stay_awake: bool = True,
        lock_screen_orientation: int = 0,
        block_frame: bool = False,
        flip: bool = False,
    ):
        self.device = device
        self.max_width = int(max_width)
        self.bitrate = int(bitrate)
        self.max_fps = int(max_fps)
        self.stayAwake = bool(stay_awake)
        self.lock_screen_orientation = int(lock_screen_orientation)
        # Aliases for scrcpy-client compatibility
        self.stay_awake = self.stayAwake
        self.max_width_alias = self.max_width

        self.alive: bool = False
        self.video_socket: Optional[socket.socket] = None
        self.control_socket: Optional[socket.socket] = None
        self.control: Optional[_ControlProtocol] = None
        self.width: int = 0
        self.height: int = 0
        self.device_name: str = ""

        self._listeners = {EVENT_FRAME: [], EVENT_INIT: []}
        self._frame_thread: Optional[threading.Thread] = None
        self._server_proc: Optional[subprocess.Popen] = None
        self._stop_event = threading.Event()

        # Lazy-init PyAV decoder
        self._codec = None

    # === Listener management ===
    def add_listener(self, event: str, callback: Callable) -> None:
        if event in self._listeners:
            self._listeners[event].append(callback)
        else:
            self._listeners[event] = [callback]

    def _emit(self, event: str, *args, **kwargs) -> None:
        for cb in self._listeners.get(event, []):
            try:
                cb(*args, **kwargs)
            except Exception as e:
                print(f"[scrcpy_native] listener error on {event}: {e}")

    # === Server management ===
    def _push_server(self) -> None:
        if not os.path.exists(SERVER_JAR_LOCAL):
            raise FileNotFoundError(
                f"scrcpy-server.jar not found at {SERVER_JAR_LOCAL}. "
                f"Download scrcpy-server-v1.25 from "
                f"https://github.com/Genymobile/scrcpy/releases/tag/v1.25 "
                f"and save it there."
            )
        self.device.push(SERVER_JAR_LOCAL, SERVER_JAR_REMOTE)
        print(f"[scrcpy_native] Server jar pushed ({os.path.getsize(SERVER_JAR_LOCAL)} bytes).")

    def _start_server(self) -> None:
        """Start scrcpy server on device via `adb shell` (background process)."""
        args = [
            f"log=info",
            f"max_size={self.max_width}",
            f"max_fps={self.max_fps}",
            f"video_bit_rate={self.bitrate}",
            "tunnel_forward=true",
            "send_frame_meta=false",
            f"stay_awake={str(self.stayAwake).lower()}",
            "power_off_on_close=false",
            f"lock_video_orientation={self.lock_screen_orientation}",
            "clipboard_autosync=false",
        ]
        cmd = (
            f"CLASSPATH={SERVER_JAR_REMOTE} "
            f"app_process / com.genymobile.scrcpy.Server {SCRCPY_VERSION} "
            + " ".join(args)
        )
        adb_path = _find_adb_path()
        full_cmd = [adb_path, "-s", self.device.serial, "shell", cmd]
        creationflags = 0
        if os.name == "nt":
            # Hide the adb.exe console window on Windows
            try:
                creationflags = subprocess.CREATE_NO_WINDOW
            except AttributeError:
                creationflags = 0
        # Capturamos stderr para ver el error real del server si crashea
        try:
            self._server_proc = subprocess.Popen(
                full_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        except FileNotFoundError:
            # Fallback: usar shell=True con la cadena completa
            shell_cmd = f'"{adb_path}" -s {self.device.serial} shell "{cmd}"'
            self._server_proc = subprocess.Popen(
                shell_cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        print(f"[scrcpy_native] Server process started (pid={self._server_proc.pid}).")

        # Lanzamos un hilo para ir vaciando el stdout/stderr del server
        # y mostrarlo (solo las primeras lineas, para debug)
        def _drain_server_output():
            try:
                count = 0
                for line in iter(self._server_proc.stdout.readline, b''):
                    if not line:
                        break
                    try:
                        text = line.decode("utf-8", errors="replace").rstrip()
                    except Exception:
                        text = repr(line)
                    if text:
                        print(f"[scrcpy_native:server] {text}")
                        count += 1
                        if count >= 20:
                            break
            except Exception as e:
                print(f"[scrcpy_native] drain error: {e}")
        t = threading.Thread(target=_drain_server_output, daemon=True, name="scrcpy-server-out")
        t.start()

    def _setup_forward(self) -> None:
        """Forward PC port 27183 -> device localabstract:scrcpy."""
        # Clean up any stale forward
        try:
            self.device.remove_forward(f"tcp:{LOCAL_PORT}")
        except Exception:
            pass
        try:
            self.device.forward(f"tcp:{LOCAL_PORT}", "localabstract:scrcpy")
        except Exception as e:
            raise ConnectionError(f"failed to set up adb forward: {e}")
        print(f"[scrcpy_native] Port forward tcp:{LOCAL_PORT} -> localabstract:scrcpy.")

    # === Socket connection ===
    def _connect_video_socket(self, timeout_s: int = 30) -> None:
        """Connect to the video socket (PC port 27183)."""
        deadline = time.time() + timeout_s
        last_err = None
        while time.time() < deadline and not self._stop_event.is_set():
            try:
                s = socket.create_connection(("127.0.0.1", LOCAL_PORT), timeout=2)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.settimeout(1.0)  # non-blocking-ish for stop event responsiveness
                self.video_socket = s
                print(f"[scrcpy_native] Video socket connected.")
                return
            except Exception as e:
                last_err = e
                time.sleep(0.2)
        raise ConnectionError(
            f"Failed to connect scrcpy video socket after {timeout_s}s. "
            f"Last error: {last_err}"
        )

    def _read_initial_metadata(self) -> None:
        """Read dummy byte, device name (64 bytes), and resolution (4 bytes)."""
        # Dummy byte (must be 0x00)
        dummy = _recv_exactly(self.video_socket, 1, self._stop_event)
        if not dummy or dummy != b"\x00":
            raise ConnectionError(
                f"Did not receive valid Dummy Byte! Got: {dummy!r}"
            )

        # Device name (64 bytes, null-padded UTF-8)
        name_bytes = _recv_exactly(self.video_socket, 64, self._stop_event)
        self.device_name = name_bytes.decode("utf-8", errors="ignore").rstrip("\x00")

        # Resolution (4 bytes: width uint16 BE + height uint16 BE)
        res_bytes = _recv_exactly(self.video_socket, 4, self._stop_event)
        self.width, self.height = struct.unpack(">HH", res_bytes)

        print(
            f"[scrcpy_native] Device: {self.device_name!r}, "
            f"Resolution: {self.width}x{self.height}"
        )

    def _connect_control_socket(self, timeout_s: int = 10) -> None:
        """Connect control socket (second connection to same forwarded port)."""
        deadline = time.time() + timeout_s
        while time.time() < deadline and not self._stop_event.is_set():
            try:
                s = socket.create_connection(("127.0.0.1", LOCAL_PORT), timeout=2)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.control_socket = s
                self.control = _ControlProtocol(s, self.width, self.height)
                print(f"[scrcpy_native] Control socket connected.")
                return
            except Exception:
                time.sleep(0.2)
        raise ConnectionError(
            f"Failed to connect control socket after {timeout_s}s."
        )

    # === Decode loop ===
    def _decode_loop(self) -> None:
        """Read raw H.264 stream from video socket and decode with PyAV."""
        if av is None:
            print("[scrcpy_native] PyAV not installed! Cannot decode video.")
            self.alive = False
            return

        try:
            self._codec = av.CodecContext.create("h264", "r")
        except Exception as e:
            print(f"[scrcpy_native] Failed to create h264 codec: {e}")
            self.alive = False
            return

        consecutive_errors = 0
        while not self._stop_event.is_set() and self.alive:
            try:
                data = self.video_socket.recv(RECV_CHUNK)
                if not data:
                    if not self._stop_event.is_set():
                        print("[scrcpy_native] Video socket closed by server.")
                    break
                consecutive_errors = 0

                # Feed data to the parser; get complete packets
                try:
                    packets = self._codec.parse(data)
                except Exception:
                    packets = []

                for packet in packets:
                    try:
                        frames = self._codec.decode(packet)
                    except Exception:
                        frames = []
                    for frame in frames:
                        try:
                            img = frame.to_ndarray(format="bgr24")
                            self._emit(EVENT_FRAME, img)
                        except Exception as e:
                            print(f"[scrcpy_native] frame conversion failed: {e}")
            except socket.timeout:
                # Normal during idle periods; just check stop event
                continue
            except ConnectionError as e:
                if not self._stop_event.is_set():
                    print(f"[scrcpy_native] Video stream disconnected: {e}")
                break
            except Exception as e:
                consecutive_errors += 1
                if not self._stop_event.is_set():
                    print(f"[scrcpy_native] decode loop error ({consecutive_errors}): {e}")
                if consecutive_errors > 10:
                    print("[scrcpy_native] Too many consecutive errors, stopping decode loop.")
                    break
                time.sleep(0.1)

        self.alive = False

    # === Public API ===
    def start(self, threaded: bool = False, daemon_threaded: bool = False) -> None:
        """Start the scrcpy client. Always runs the decode loop in a daemon thread."""
        try:
            self._stop_event.clear()
            print("[scrcpy_native] Pushing server jar...")
            self._push_server()

            print("[scrcpy_native] Setting up port forward...")
            self._setup_forward()

            print("[scrcpy_native] Starting server on device...")
            self._start_server()

            print("[scrcpy_native] Connecting video socket (up to 30s)...")
            self._connect_video_socket(timeout_s=30)

            print("[scrcpy_native] Reading initial metadata...")
            self._read_initial_metadata()

            print("[scrcpy_native] Connecting control socket...")
            self._connect_control_socket(timeout_s=10)

            self.alive = True
            self._emit(EVENT_INIT)

            print("[scrcpy_native] Starting decode thread...")
            self._frame_thread = threading.Thread(
                target=self._decode_loop, daemon=True, name="scrcpy-decode"
            )
            self._frame_thread.start()

            print("[scrcpy_native] Client started successfully.")
        except Exception as e:
            self.stop()
            raise ConnectionError(f"[scrcpy_native] Failed to start: {e}") from e

    def stop(self) -> None:
        """Stop the scrcpy client and clean up all resources."""
        if not self.alive and not self.video_socket and not self._server_proc:
            # Already stopped
            return

        self._stop_event.set()
        self.alive = False

        if self.control:
            try:
                self.control.close()
            except Exception:
                pass
            self.control = None

        if self.control_socket:
            try:
                self.control_socket.close()
            except Exception:
                pass
            self.control_socket = None

        if self.video_socket:
            try:
                self.video_socket.close()
            except Exception:
                pass
            self.video_socket = None

        if self._server_proc:
            try:
                self._server_proc.terminate()
                try:
                    self._server_proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._server_proc.kill()
                    try:
                        self._server_proc.wait(timeout=1)
                    except Exception:
                        pass
            except Exception:
                pass
            self._server_proc = None

        try:
            self.device.remove_forward(f"tcp:{LOCAL_PORT}")
        except Exception:
            pass

        if self._frame_thread and self._frame_thread.is_alive():
            self._frame_thread.join(timeout=1.0)

        print("[scrcpy_native] Client stopped.")
