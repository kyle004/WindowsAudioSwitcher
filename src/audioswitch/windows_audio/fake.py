"""In-memory backend used by the unit tests and for running the UI on non-Windows machines."""
from __future__ import annotations

import threading
from typing import Callable

from ..device_service import DeviceEvent
from ..models import (
    CAPTURE,
    RENDER,
    ROLE_COMMUNICATIONS,
    ROLE_CONSOLE,
    ROLE_MULTIMEDIA,
    STATE_ACTIVE,
    STATE_UNPLUGGED,
    AudioDevice,
    AudioSession,
)


class FakeAudioBackend:
    name = "fake"
    supports_notifications = True

    def __init__(self, devices: list[AudioDevice] | None = None):
        self._lock = threading.RLock()
        self._callbacks: list[Callable[[DeviceEvent], None]] = []
        self.fail_set_default = False  # tests flip this to simulate IPolicyConfig failures
        self.set_default_calls: list[tuple[str, tuple[str, ...]]] = []
        self._volumes: dict[str, float] = {}
        self._mutes: dict[str, bool] = {}
        self._sessions: dict[str, AudioSession] = {}
        self._devices: dict[str, AudioDevice] = {}
        for d in devices if devices is not None else self.sample_devices():
            self._devices[d.id] = d
            self._volumes.setdefault(d.id, 0.5)
            self._mutes.setdefault(d.id, False)
        for s in self.sample_sessions():
            self._sessions[s.key] = s

    # ---- sample data ----------------------------------------------------
    @staticmethod
    def sample_devices() -> list[AudioDevice]:
        return [
            AudioDevice("{out-headset}", "Headset Earphone (Arctis 7)", RENDER, STATE_ACTIVE, True, True, True, "SteelSeries Arctis 7"),
            AudioDevice("{out-speakers}", "Speakers (Realtek High Definition Audio)", RENDER, STATE_ACTIVE, description="Realtek HD Audio"),
            AudioDevice("{out-hdmi}", "LG ULTRAGEAR (NVIDIA High Definition Audio)", RENDER, STATE_ACTIVE, description="NVIDIA HD Audio"),
            AudioDevice("{out-bt}", "Headphones (WH-1000XM4 Stereo)", RENDER, STATE_UNPLUGGED, description="Bluetooth"),
            AudioDevice("{in-headset}", "Headset Microphone (Arctis 7)", CAPTURE, STATE_ACTIVE, True, True, True, "SteelSeries Arctis 7"),
            AudioDevice("{in-usb}", "Microphone (Blue Yeti)", CAPTURE, STATE_ACTIVE, description="Blue Yeti"),
            AudioDevice("{in-webcam}", "Microphone (C920 HD Pro Webcam)", CAPTURE, STATE_ACTIVE, description="Logitech C920"),
        ]

    @staticmethod
    def sample_sessions() -> list[AudioSession]:
        return [
            AudioSession("sys", 0, "System Sounds", 0.6, False, is_system=True),
            AudioSession("4120:Discord", 4120, "Discord", 0.8, False),
            AudioSession("5330:chrome", 5330, "Google Chrome", 1.0, False),
            AudioSession("6001:Spotify", 6001, "Spotify", 0.45, False),
            AudioSession("7777:game", 7777, "Counter-Strike 2", 0.7, True),
        ]

    # ---- AudioBackend -----------------------------------------------------
    def list_devices(self) -> list[AudioDevice]:
        with self._lock:
            return list(self._devices.values())

    def set_default(self, device_id: str, roles: tuple[str, ...]) -> None:
        with self._lock:
            self.set_default_calls.append((device_id, tuple(roles)))
            if self.fail_set_default:
                raise RuntimeError("E_FAIL (simulated IPolicyConfig failure)")
            target = self._devices[device_id]
            for did, dev in list(self._devices.items()):
                if dev.direction != target.direction:
                    continue
                current = set(dev.default_roles)
                if did == device_id:
                    current |= set(roles)
                else:
                    current -= set(roles)
                self._devices[did] = dev.with_defaults(tuple(current))
        for role in roles:
            self._emit(DeviceEvent("default_changed", device_id, target.direction, role))

    def get_volume(self, device_id: str) -> float | None:
        return self._volumes.get(device_id)

    def set_volume(self, device_id: str, level: float) -> None:
        if device_id not in self._devices:
            raise RuntimeError("no such device")
        self._volumes[device_id] = level

    def get_mute(self, device_id: str) -> bool | None:
        return self._mutes.get(device_id)

    def set_mute(self, device_id: str, muted: bool) -> None:
        if device_id not in self._devices:
            raise RuntimeError("no such device")
        self._mutes[device_id] = muted

    def list_sessions(self) -> list[AudioSession]:
        return list(self._sessions.values())

    def set_session_volume(self, key: str, level: float) -> None:
        s = self._sessions[key]
        self._sessions[key] = AudioSession(s.key, s.pid, s.name, level, s.muted, s.state, s.is_system)

    def set_session_mute(self, key: str, muted: bool) -> None:
        s = self._sessions[key]
        self._sessions[key] = AudioSession(s.key, s.pid, s.name, s.volume, muted, s.state, s.is_system)

    def subscribe(self, callback: Callable[[DeviceEvent], None]) -> bool:
        self._callbacks.append(callback)
        return True

    def close(self) -> None:
        self._callbacks.clear()

    # ---- simulation helpers (tests / dev menu) ------------------------------
    def _emit(self, event: DeviceEvent) -> None:
        for cb in list(self._callbacks):
            cb(event)

    def simulate_unplug(self, device_id: str) -> None:
        with self._lock:
            dev = self._devices[device_id]
            was_default = dev.default_roles
            self._devices[device_id] = dev.with_state(STATE_UNPLUGGED).with_defaults(())
            fallback = None
            if was_default:
                # Windows picks another active endpoint of the same direction.
                for other in self._devices.values():
                    if other.direction == dev.direction and other.is_active and other.id != device_id:
                        fallback = other
                        break
                if fallback is not None:
                    self._devices[fallback.id] = fallback.with_defaults(tuple(set(fallback.default_roles) | set(was_default)))
        self._emit(DeviceEvent("state_changed", device_id, dev.direction, new_state=STATE_UNPLUGGED))
        if fallback is not None:
            for role in was_default:
                self._emit(DeviceEvent("default_changed", fallback.id, dev.direction, role))

    def simulate_plug(self, device_id: str) -> None:
        with self._lock:
            dev = self._devices[device_id]
            self._devices[device_id] = dev.with_state(STATE_ACTIVE)
        self._emit(DeviceEvent("state_changed", device_id, dev.direction, new_state=STATE_ACTIVE))

    def simulate_external_default_change(self, device_id: str) -> None:
        """Pretend the user changed the default in the Windows Sound panel."""
        self.set_default(device_id, (ROLE_CONSOLE, ROLE_MULTIMEDIA, ROLE_COMMUNICATIONS))
