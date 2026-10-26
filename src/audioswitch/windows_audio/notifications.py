"""IMMNotificationClient bridge built on pycaw.callbacks.MMNotificationClient.

Every hook is written defensively: pycaw has renamed hook parameters between
releases, so we accept *args and pull the device id / state out by type. If
pycaw's callback base class is missing we return None and the controller
falls back to polling.
"""
from __future__ import annotations

import logging
from typing import Callable

from ..device_service import DeviceEvent
from ..models import CAPTURE, RENDER, STATE_ACTIVE, STATE_DISABLED, STATE_NOT_PRESENT, STATE_UNPLUGGED

log = logging.getLogger(__name__)

_STATE = {1: STATE_ACTIVE, 2: STATE_DISABLED, 4: STATE_NOT_PRESENT, 8: STATE_UNPLUGGED}
_FLOW = {0: RENDER, 1: CAPTURE}
_ROLE = {0: "console", 1: "multimedia", 2: "communications"}


def _as_int(value) -> int | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        value = value.value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_str(args) -> str | None:
    for a in args:
        if isinstance(a, str):
            return a
    return None


def make_notification_client(callback: Callable[[DeviceEvent], None]):
    try:
        from pycaw.callbacks import MMNotificationClient  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        log.warning("pycaw.callbacks unavailable (%s); device notifications disabled", exc)
        return None

    class _Client(MMNotificationClient):  # type: ignore[misc]
        def _safe(self, event: DeviceEvent) -> None:
            try:
                callback(event)
            except Exception:  # noqa: BLE001
                log.exception("device event callback failed")

        def on_default_device_changed(self, *args, **kwargs):
            ints = [v for v in (_as_int(a) for a in args if not isinstance(a, str)) if v is not None]
            flow = _FLOW.get(ints[0]) if ints else None
            # pycaw passes (flow, flow_id, role, role_id, device_id): role is the 3rd int-ish arg
            role = None
            if len(ints) >= 3:
                role = _ROLE.get(ints[2])
            elif len(ints) == 2:
                role = _ROLE.get(ints[1])
            self._safe(DeviceEvent("default_changed", _first_str(args) or kwargs.get("default_device_id"), flow, role))

        def on_device_added(self, *args, **kwargs):
            self._safe(DeviceEvent("added", _first_str(args) or kwargs.get("added_device_id")))

        def on_device_removed(self, *args, **kwargs):
            self._safe(DeviceEvent("removed", _first_str(args) or kwargs.get("removed_device_id")))

        def on_device_state_changed(self, *args, **kwargs):
            ints = [v for v in (_as_int(a) for a in args if not isinstance(a, str)) if v is not None]
            state = _STATE.get(ints[0]) if ints else None
            self._safe(DeviceEvent("state_changed", _first_str(args) or kwargs.get("device_id"), None, None, state))

        def on_property_value_changed(self, *args, **kwargs):
            # Fires constantly (volume meters etc.); only forward the id so the
            # controller can refresh names lazily.
            self._safe(DeviceEvent("property_changed", _first_str(args) or kwargs.get("device_id")))

    try:
        return _Client()
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not create notification client: %s", exc)
        return None
