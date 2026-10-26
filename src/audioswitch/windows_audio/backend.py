"""Windows Core Audio backend (MMDevice + EndpointVolume + AudioSession APIs).

Documented interfaces come from `pycaw` (a thin, well-tested comtypes wrapper).
The only undocumented piece, IPolicyConfig, lives in `policy_config.py`.

Threading: this object is used from the Qt main thread (STA). COM notification
callbacks arrive on a COM worker thread and are forwarded verbatim; the
controller marshals them onto the Qt thread.
"""
from __future__ import annotations

import logging
import threading
from ctypes import POINTER, cast
from typing import Callable

import comtypes
from pycaw.pycaw import (  # type: ignore[import-not-found]
    AudioUtilities,
    IAudioEndpointVolume,
)

from ..device_service import BackendError, DeviceEvent
from ..models import (
    ALL_ROLES,
    CAPTURE,
    RENDER,
    STATE_ACTIVE,
    STATE_DISABLED,
    STATE_NOT_PRESENT,
    STATE_UNPLUGGED,
    AudioDevice,
    AudioSession,
)
from .policy_config import DefaultDeviceSetter

log = logging.getLogger(__name__)

E_RENDER, E_CAPTURE = 0, 1
EROLE = {"console": 0, "multimedia": 1, "communications": 2}
DEVICE_STATE_MASK_ALL = 0xF
STATE_MAP = {1: STATE_ACTIVE, 2: STATE_DISABLED, 4: STATE_NOT_PRESENT, 8: STATE_UNPLUGGED}

# PROPERTYKEYs as pycaw stringifies them: "{fmtid} pid"
PKEY_DEVICE_FRIENDLY_NAME = "{a45c254e-df1c-4efd-8020-67d146a850e0} 14"
PKEY_DEVICE_DESC = "{a45c254e-df1c-4efd-8020-67d146a850e0} 2"
PKEY_INTERFACE_FRIENDLY_NAME = "{026e516e-b814-414b-83cd-856d6fef4822} 2"


def _hresult_text(exc: BaseException) -> str:
    code = getattr(exc, "hresult", None)
    if isinstance(code, int):
        return f"{exc} (HRESULT 0x{code & 0xFFFFFFFF:08X})"
    return str(exc)


class WindowsAudioBackend:
    name = "windows"
    supports_notifications = True

    def __init__(self) -> None:
        self._enum = None
        self._setter = DefaultDeviceSetter()
        self._notify_client = None
        self._lock = threading.RLock()
        self._sessions_cache: dict[str, object] = {}

    # ---- COM plumbing -----------------------------------------------------
    def _enumerator(self):
        if self._enum is None:
            try:
                self._enum = AudioUtilities.GetDeviceEnumerator()
            except Exception as exc:  # noqa: BLE001
                raise BackendError(f"Windows Audio service unavailable: {_hresult_text(exc)}") from exc
        return self._enum

    def _device_ptr(self, device_id: str):
        try:
            return self._enumerator().GetDevice(device_id)
        except Exception as exc:  # noqa: BLE001
            raise BackendError(f"Device not found: {_hresult_text(exc)}") from exc

    def _default_id(self, flow: int, role: int) -> str | None:
        try:
            dev = self._enumerator().GetDefaultAudioEndpoint(flow, role)
            return dev.GetId() if dev is not None else None
        except Exception:  # noqa: BLE001 - no default for this flow (no devices)
            return None

    # ---- AudioBackend -----------------------------------------------------
    def list_devices(self) -> list[AudioDevice]:
        with self._lock:
            enum = self._enumerator()
            out: list[AudioDevice] = []
            for flow, direction in ((E_RENDER, RENDER), (E_CAPTURE, CAPTURE)):
                defaults = {role: self._default_id(flow, erole) for role, erole in EROLE.items()}
                try:
                    coll = enum.EnumAudioEndpoints(flow, DEVICE_STATE_MASK_ALL)
                    count = coll.GetCount()
                except Exception as exc:  # noqa: BLE001
                    raise BackendError(f"EnumAudioEndpoints failed: {_hresult_text(exc)}") from exc
                for i in range(count):
                    try:
                        dev = coll.Item(i)
                        info = AudioUtilities.CreateDevice(dev)
                    except Exception as exc:  # noqa: BLE001
                        log.debug("skipping endpoint %d: %s", i, exc)
                        continue
                    props = getattr(info, "properties", {}) or {}
                    name = props.get(PKEY_DEVICE_FRIENDLY_NAME) or getattr(info, "FriendlyName", None) or info.id
                    desc = props.get(PKEY_INTERFACE_FRIENDLY_NAME) or props.get(PKEY_DEVICE_DESC) or ""
                    raw_state = info.state.value if hasattr(info.state, "value") else int(info.state)
                    out.append(
                        AudioDevice(
                            id=str(info.id),
                            name=str(name),
                            direction=direction,
                            state=STATE_MAP.get(int(raw_state), STATE_NOT_PRESENT),
                            is_default_console=defaults["console"] == info.id,
                            is_default_multimedia=defaults["multimedia"] == info.id,
                            is_default_communications=defaults["communications"] == info.id,
                            description=str(desc),
                        )
                    )
            return out

    def set_default(self, device_id: str, roles: tuple[str, ...]) -> None:
        roles = tuple(r for r in roles if r in ALL_ROLES)
        with self._lock:
            self._setter.set_default(device_id, roles)

    def _endpoint_volume(self, device_id: str):
        dev = self._device_ptr(device_id)
        try:
            iface = dev.Activate(IAudioEndpointVolume._iid_, comtypes.CLSCTX_ALL, None)
            return cast(iface, POINTER(IAudioEndpointVolume))
        except Exception as exc:  # noqa: BLE001
            raise BackendError(f"Endpoint volume unavailable: {_hresult_text(exc)}") from exc

    def get_volume(self, device_id: str) -> float | None:
        with self._lock:
            return float(self._endpoint_volume(device_id).GetMasterVolumeLevelScalar())

    def set_volume(self, device_id: str, level: float) -> None:
        with self._lock:
            self._endpoint_volume(device_id).SetMasterVolumeLevelScalar(float(level), None)

    def get_mute(self, device_id: str) -> bool | None:
        with self._lock:
            return bool(self._endpoint_volume(device_id).GetMute())

    def set_mute(self, device_id: str, muted: bool) -> None:
        with self._lock:
            self._endpoint_volume(device_id).SetMute(1 if muted else 0, None)

    # ---- sessions (default render device) ----------------------------------
    def list_sessions(self) -> list[AudioSession]:
        with self._lock:
            try:
                raw = AudioUtilities.GetAllSessions()
            except Exception as exc:  # noqa: BLE001
                raise BackendError(f"Audio sessions unavailable: {_hresult_text(exc)}") from exc
            out: list[AudioSession] = []
            cache: dict[str, object] = {}
            for s in raw:
                try:
                    vol = s.SimpleAudioVolume
                    pid = int(getattr(s, "ProcessId", 0) or 0)
                    proc = getattr(s, "Process", None)
                    pname = ""
                    if proc is not None:
                        try:
                            pname = proc.name()
                        except Exception:  # noqa: BLE001
                            pname = ""
                    display = getattr(s, "DisplayName", "") or ""
                    if display.startswith("@"):
                        display = ""  # resource reference, e.g. system sounds
                    is_system = pid == 0
                    name = "System Sounds" if is_system else (_pretty_process_name(pname) or display or f"PID {pid}")
                    key = str(getattr(s, "InstanceIdentifier", "") or f"{pid}:{pname}")
                    state_val = getattr(s, "State", 1)
                    state = {0: "inactive", 1: "active", 2: "expired"}.get(int(state_val), "active")
                    out.append(
                        AudioSession(
                            key=key,
                            pid=pid,
                            name=name,
                            volume=float(vol.GetMasterVolume()),
                            muted=bool(vol.GetMute()),
                            state=state,
                            is_system=is_system,
                        )
                    )
                    cache[key] = vol
                except Exception as exc:  # noqa: BLE001
                    log.debug("skipping session: %s", exc)
            self._sessions_cache = cache
            return out

    def _session_volume(self, key: str):
        vol = self._sessions_cache.get(key)
        if vol is None:
            self.list_sessions()
            vol = self._sessions_cache.get(key)
        if vol is None:
            raise BackendError("That application is no longer playing audio.")
        return vol

    def set_session_volume(self, key: str, level: float) -> None:
        with self._lock:
            self._session_volume(key).SetMasterVolume(float(level), None)

    def set_session_mute(self, key: str, muted: bool) -> None:
        with self._lock:
            self._session_volume(key).SetMute(1 if muted else 0, None)

    # ---- notifications -----------------------------------------------------
    def subscribe(self, callback: Callable[[DeviceEvent], None]) -> bool:
        from .notifications import make_notification_client

        client = make_notification_client(callback)
        if client is None:
            return False
        try:
            self._enumerator().RegisterEndpointNotificationCallback(client)
        except Exception as exc:  # noqa: BLE001
            log.warning("RegisterEndpointNotificationCallback failed: %s", _hresult_text(exc))
            return False
        self._notify_client = client
        return True

    def close(self) -> None:
        if self._notify_client is not None and self._enum is not None:
            try:
                self._enum.UnregisterEndpointNotificationCallback(self._notify_client)
            except Exception:  # noqa: BLE001
                pass
        self._notify_client = None
        self._enum = None

    # ---- diagnostics -------------------------------------------------------
    def describe_setter(self) -> str:
        return self._setter.describe()

    def probe_setter(self) -> str:
        """Instantiate IPolicyConfig without switching anything. Used by --doctor."""
        self._setter._ensure_client()  # noqa: SLF001
        return self._setter.describe()


def _pretty_process_name(pname: str) -> str:
    if not pname:
        return ""
    base = pname[:-4] if pname.lower().endswith(".exe") else pname
    known = {"chrome": "Google Chrome", "msedge": "Microsoft Edge", "firefox": "Firefox", "Discord": "Discord", "Spotify": "Spotify", "steam": "Steam", "Teams": "Microsoft Teams", "ms-teams": "Microsoft Teams", "Zoom": "Zoom", "vlc": "VLC"}
    return known.get(base, base)
