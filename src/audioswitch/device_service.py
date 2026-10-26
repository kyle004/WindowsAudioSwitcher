"""Backend-agnostic device service.

`AudioBackend` is the narrow interface every platform backend implements
(`windows_audio.backend.WindowsAudioBackend`, `windows_audio.fake.FakeAudioBackend`).
`DeviceService` adds caching, aliases/hiding, default lookups and change
detection on top of it. No Qt in here; the controller bridges to Qt.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable, Protocol

from .models import (
    ALL_ROLES,
    CAPTURE,
    PRIMARY_ROLE,
    RENDER,
    AppConfig,
    AudioDevice,
    AudioSession,
)

log = logging.getLogger(__name__)


class BackendError(RuntimeError):
    """Any failure talking to the OS audio stack. Message is user-presentable."""


@dataclass(frozen=True)
class DeviceEvent:
    kind: str  # default_changed | added | removed | state_changed | property_changed | poll
    device_id: str | None = None
    direction: str | None = None
    role: str | None = None
    new_state: str | None = None


EventCallback = Callable[[DeviceEvent], None]


class AudioBackend(Protocol):
    name: str
    supports_notifications: bool

    def list_devices(self) -> list[AudioDevice]: ...
    def set_default(self, device_id: str, roles: tuple[str, ...]) -> None: ...
    def get_volume(self, device_id: str) -> float | None: ...
    def set_volume(self, device_id: str, level: float) -> None: ...
    def get_mute(self, device_id: str) -> bool | None: ...
    def set_mute(self, device_id: str, muted: bool) -> None: ...
    def list_sessions(self) -> list[AudioSession]: ...
    def set_session_volume(self, key: str, level: float) -> None: ...
    def set_session_mute(self, key: str, muted: bool) -> None: ...
    def subscribe(self, callback: EventCallback) -> bool: ...
    def close(self) -> None: ...


def role_flags(device: AudioDevice) -> dict[str, bool]:
    return {
        "console": device.is_default_console,
        "multimedia": device.is_default_multimedia,
        "communications": device.is_default_communications,
    }


class DeviceService:
    def __init__(self, backend: AudioBackend, config: AppConfig):
        self.backend = backend
        self.config = config
        self._devices: list[AudioDevice] = []
        self._lock = threading.RLock()

    # ---- enumeration ----------------------------------------------------
    def refresh(self) -> list[AudioDevice]:
        try:
            devices = self.backend.list_devices()
        except Exception as exc:  # noqa: BLE001 - surface everything as BackendError
            raise BackendError(f"Could not enumerate audio devices: {exc}") from exc
        with self._lock:
            self._devices = list(devices)
        return list(devices)

    @property
    def devices(self) -> list[AudioDevice]:
        with self._lock:
            return list(self._devices)

    def device(self, device_id: str | None) -> AudioDevice | None:
        if device_id is None:
            return None
        for d in self.devices:
            if d.id == device_id:
                return d
        return None

    def devices_for(self, direction: str, include_inactive: bool = False, include_hidden: bool = False) -> list[AudioDevice]:
        hidden = set(self.config.hidden_device_ids)
        out = []
        for d in self.devices:
            if d.direction != direction:
                continue
            if not include_inactive and not d.is_active:
                continue
            if not include_hidden and d.id in hidden:
                continue
            out.append(d)
        return sorted(out, key=lambda d: self.display_name(d).lower())

    def default_for(self, direction: str, role: str = PRIMARY_ROLE) -> AudioDevice | None:
        for d in self.devices:
            if d.direction == direction and role_flags(d).get(role):
                return d
        return None

    def current_output(self) -> AudioDevice | None:
        return self.default_for(RENDER)

    def current_input(self) -> AudioDevice | None:
        return self.default_for(CAPTURE)

    def display_name(self, device: AudioDevice | None) -> str:
        if device is None:
            return "None"
        return self.config.display_name(device)

    def find_by_name_or_id(self, text: str, direction: str | None = None) -> AudioDevice | None:
        """Loose lookup used by the CLI: exact id, exact alias/name, then substring."""
        needle = text.strip().lower()
        pool = [d for d in self.devices if direction is None or d.direction == direction]
        for d in pool:
            if d.id.lower() == needle:
                return d
        for d in pool:
            if self.display_name(d).lower() == needle or d.name.lower() == needle:
                return d
        matches = [d for d in pool if d.is_active and (needle in self.display_name(d).lower() or needle in d.name.lower())]
        if len(matches) == 1:
            return matches[0]
        return None

    # ---- switching ------------------------------------------------------
    def set_default(self, device_id: str, roles: tuple[str, ...] | None = None) -> AudioDevice:
        """Set `device_id` as default for `roles` and VERIFY by re-enumerating."""
        roles = tuple(r for r in (roles or tuple(self.config.default_roles_to_update)) if r in ALL_ROLES)
        if not roles:
            roles = tuple(ALL_ROLES)
        target = self.device(device_id)
        if target is None:
            raise BackendError("That device is not connected.")
        if not target.is_active:
            raise BackendError(f"'{self.display_name(target)}' is {target.state}.")
        try:
            self.backend.set_default(device_id, roles)
        except BackendError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BackendError(f"Windows refused to change the default device: {exc}") from exc
        after = self.refresh()
        actual = next((d for d in after if d.id == device_id), None)
        if actual is None:
            raise BackendError("Device disappeared during the switch.")
        flags = role_flags(actual)
        missing = [r for r in roles if not flags.get(r)]
        if missing:
            raise BackendError(
                f"Switch to '{self.display_name(actual)}' was not applied for role(s): {', '.join(missing)}."
            )
        return actual

    # ---- volume ---------------------------------------------------------
    def get_volume(self, device_id: str) -> float | None:
        try:
            return self.backend.get_volume(device_id)
        except Exception as exc:  # noqa: BLE001
            log.debug("get_volume(%s) failed: %s", device_id, exc)
            return None

    def set_volume(self, device_id: str, level: float) -> None:
        level = min(1.0, max(0.0, float(level)))
        try:
            self.backend.set_volume(device_id, level)
        except Exception as exc:  # noqa: BLE001
            raise BackendError(f"Could not set volume: {exc}") from exc

    def get_mute(self, device_id: str) -> bool | None:
        try:
            return self.backend.get_mute(device_id)
        except Exception as exc:  # noqa: BLE001
            log.debug("get_mute(%s) failed: %s", device_id, exc)
            return None

    def set_mute(self, device_id: str, muted: bool) -> None:
        try:
            self.backend.set_mute(device_id, muted)
        except Exception as exc:  # noqa: BLE001
            raise BackendError(f"Could not change mute: {exc}") from exc

    # ---- sessions -------------------------------------------------------
    def list_sessions(self) -> list[AudioSession]:
        try:
            return self.backend.list_sessions()
        except Exception as exc:  # noqa: BLE001
            log.debug("list_sessions failed: %s", exc)
            return []

    def set_session_volume(self, key: str, level: float) -> None:
        self.backend.set_session_volume(key, min(1.0, max(0.0, level)))

    def set_session_mute(self, key: str, muted: bool) -> None:
        self.backend.set_session_mute(key, muted)

    # ---- events ---------------------------------------------------------
    def subscribe(self, callback: EventCallback) -> bool:
        try:
            return bool(self.backend.subscribe(callback))
        except Exception as exc:  # noqa: BLE001
            log.warning("Device notifications unavailable, will poll instead: %s", exc)
            return False

    def close(self) -> None:
        try:
            self.backend.close()
        except Exception:  # noqa: BLE001
            log.debug("backend close failed", exc_info=True)


def diff_devices(before: list[AudioDevice], after: list[AudioDevice]) -> list[DeviceEvent]:
    """Compute events between two snapshots (used by the polling fallback)."""
    events: list[DeviceEvent] = []
    b = {d.id: d for d in before}
    a = {d.id: d for d in after}
    for did, dev in a.items():
        if did not in b:
            events.append(DeviceEvent("added", did, dev.direction, new_state=dev.state))
        elif b[did].state != dev.state:
            events.append(DeviceEvent("state_changed", did, dev.direction, new_state=dev.state))
    for did, dev in b.items():
        if did not in a:
            events.append(DeviceEvent("removed", did, dev.direction))
    for direction in (RENDER, CAPTURE):
        for role in ALL_ROLES:
            old = next((d.id for d in before if d.direction == direction and role_flags(d)[role]), None)
            new = next((d.id for d in after if d.direction == direction and role_flags(d)[role]), None)
            if old != new:
                events.append(DeviceEvent("default_changed", new, direction, role))
    return events
