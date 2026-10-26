"""Win32 global hotkeys on a dedicated message-loop thread, plus a low-level
mouse hook that turns wheel events over the tray icon into volume changes.

Design:
* RegisterHotKey binds hotkeys to the *calling thread*, so a dedicated thread
  owns every registration and pumps its own message queue.
* The Qt side talks to it via a command queue + PostThreadMessage(WM_APP) so
  (re)registration never blocks the UI.
* Callbacks fire on the hotkey thread. The controller wraps them in a Qt
  signal (queued connection) which puts the actual switch on the UI thread.
* On non-Windows platforms `HotkeyManager` is a no-op that reports every
  binding as "unsupported on this OS" so the app still runs for development.
"""
from __future__ import annotations

import ctypes
import logging
import queue
import sys
import threading
from dataclasses import dataclass
from typing import Callable

from ..models import HotkeyBinding
from .keys import key_to_vk, modifiers_to_flags, validate_binding

log = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
WM_APP = 0x8000
WM_QUIT = 0x0012
WM_MOUSEWHEEL = 0x020A
WH_MOUSE_LL = 14
ERROR_HOTKEY_ALREADY_REGISTERED = 1409


@dataclass
class RegistrationResult:
    binding_id: str
    ok: bool
    error: str | None = None


HotkeyCallback = Callable[[str], None]
WheelCallback = Callable[[int], None]  # +1 / -1 per notch


class HotkeyManager:
    def __init__(self, on_hotkey: HotkeyCallback, on_tray_wheel: WheelCallback | None = None):
        self._on_hotkey = on_hotkey
        self._on_wheel = on_tray_wheel
        self._cmds: queue.Queue = queue.Queue()
        self._results: dict[str, RegistrationResult] = {}
        self._results_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._tray_rect: tuple[int, int, int, int] | None = None
        self._wheel_enabled = False
        self.available = sys.platform == "win32"
        self._bindings: dict[int, str] = {}  # atom id -> binding id
        self._hook = None
        self._hook_proc = None

    # ---- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if not self.available:
            log.info("Global hotkeys unavailable on %s", sys.platform)
            return
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="HotkeyThread", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def stop(self) -> None:
        if self._thread is None or self._thread_id is None:
            return
        self._cmds.put(("quit", None))
        self._poke()
        self._thread.join(timeout=3)
        self._thread = None

    # ---- public API (any thread) -------------------------------------------
    def apply_bindings(self, bindings: list[HotkeyBinding]) -> dict[str, RegistrationResult]:
        """Replace all registrations. Returns per-binding results (synchronously)."""
        if not self.available or self._thread is None:
            return {b.id: RegistrationResult(b.id, False, "Global hotkeys are only available on Windows.") for b in bindings}
        done = threading.Event()
        self._cmds.put(("apply", (list(bindings), done)))
        self._poke()
        done.wait(timeout=5)
        with self._results_lock:
            return dict(self._results)

    def set_tray_rect(self, rect: tuple[int, int, int, int] | None) -> None:
        self._tray_rect = rect

    def set_wheel_enabled(self, enabled: bool) -> None:
        self._wheel_enabled = enabled
        if self.available and self._thread is not None:
            self._cmds.put(("hook", enabled))
            self._poke()

    # ---- thread internals -----------------------------------------------------
    def _poke(self) -> None:
        if self._thread_id is not None:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_APP, 0, 0)

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        from ctypes import wintypes

        self._thread_id = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        # Force creation of the thread message queue before signalling ready.
        user32.PeekMessageW(ctypes.byref(msg), None, WM_APP, WM_APP, 0)
        self._ready.set()
        if self._wheel_enabled:
            self._install_hook()
        while True:
            rc = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if rc in (0, -1):
                break
            if msg.message == WM_HOTKEY:
                bid = self._bindings.get(int(msg.wParam))
                if bid:
                    try:
                        self._on_hotkey(bid)
                    except Exception:  # noqa: BLE001
                        log.exception("hotkey callback failed")
            elif msg.message == WM_APP:
                if not self._drain_commands():
                    break
            else:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        self._unregister_all()
        self._remove_hook()

    def _drain_commands(self) -> bool:
        while True:
            try:
                cmd, arg = self._cmds.get_nowait()
            except queue.Empty:
                return True
            if cmd == "quit":
                return False
            if cmd == "apply":
                bindings, done = arg
                self._apply(bindings)
                done.set()
            elif cmd == "hook":
                if arg:
                    self._install_hook()
                else:
                    self._remove_hook()

    def _unregister_all(self) -> None:
        user32 = ctypes.windll.user32
        for atom in list(self._bindings):
            user32.UnregisterHotKey(None, atom)
        self._bindings.clear()

    def _apply(self, bindings: list[HotkeyBinding]) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._unregister_all()
        results: dict[str, RegistrationResult] = {}
        seen: dict[tuple, str] = {}
        next_id = 1
        for b in bindings:
            if not b.enabled:
                results[b.id] = RegistrationResult(b.id, True, None)
                continue
            err = validate_binding(b.modifiers, b.key)
            if err:
                results[b.id] = RegistrationResult(b.id, False, err)
                continue
            if b.signature in seen:
                results[b.id] = RegistrationResult(b.id, False, f"Same shortcut as '{seen[b.signature]}'.")
                continue
            vk = key_to_vk(b.key)
            flags = modifiers_to_flags(b.modifiers)
            ok = user32.RegisterHotKey(None, next_id, flags, vk)
            if ok:
                self._bindings[next_id] = b.id
                results[b.id] = RegistrationResult(b.id, True, None)
                seen[b.signature] = b.id
                next_id += 1
            else:
                code = kernel32.GetLastError()
                if code == ERROR_HOTKEY_ALREADY_REGISTERED:
                    msg = f"{b.label} is already used by another program."
                else:
                    msg = f"Windows refused {b.label} (error {code})."
                results[b.id] = RegistrationResult(b.id, False, msg)
                log.warning("RegisterHotKey failed for %s: %s", b.label, msg)
        with self._results_lock:
            self._results = results

    # ---- tray wheel hook --------------------------------------------------------
    def _install_hook(self) -> None:
        if self._hook is not None or self._on_wheel is None:
            return
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        class MSLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

        HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        def proc(n_code, w_param, l_param):
            try:
                if n_code >= 0 and w_param == WM_MOUSEWHEEL and self._wheel_enabled:
                    rect = self._tray_rect
                    if rect:
                        info = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                        x, y = info.pt.x, info.pt.y
                        left, top, right, bottom = rect
                        if left <= x <= right and top <= y <= bottom:
                            delta = ctypes.c_short((info.mouseData >> 16) & 0xFFFF).value
                            if delta:
                                self._on_wheel(1 if delta > 0 else -1)
                            return 1  # swallow so the shell doesn't scroll
            except Exception:  # noqa: BLE001
                log.exception("mouse hook failed")
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        self._hook_proc = HOOKPROC(proc)
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        user32.CallNextHookEx.restype = ctypes.c_long
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        self._hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._hook_proc, kernel32.GetModuleHandleW(None), 0)
        if not self._hook:
            log.warning("SetWindowsHookEx(WH_MOUSE_LL) failed: %s", kernel32.GetLastError())
            self._hook = None

    def _remove_hook(self) -> None:
        if self._hook is not None:
            ctypes.windll.user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
            self._hook_proc = None
