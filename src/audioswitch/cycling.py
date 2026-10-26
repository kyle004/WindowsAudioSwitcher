"""Pure device-cycling logic."""
from __future__ import annotations

from .models import AudioDevice


def eligible_devices(
    devices: list[AudioDevice],
    direction: str,
    order_ids: list[str],
    hidden_ids: list[str] | None = None,
) -> list[AudioDevice]:
    """Active devices of `direction`, in the configured order.

    If `order_ids` is empty, every visible active device is eligible, sorted by
    name so the order is stable between runs.
    """
    hidden = set(hidden_ids or [])
    by_id = {d.id: d for d in devices if d.direction == direction and d.is_active}
    if order_ids:
        return [by_id[i] for i in order_ids if i in by_id and i not in hidden]
    return sorted((d for d in by_id.values() if d.id not in hidden), key=lambda d: d.name.lower())


def next_device(
    devices: list[AudioDevice],
    direction: str,
    order_ids: list[str],
    current_id: str | None,
    hidden_ids: list[str] | None = None,
    step: int = 1,
) -> AudioDevice | None:
    """Return the device after `current_id` in the cycle, wrapping around.

    Returns None when fewer than one eligible device exists, or when the only
    eligible device is already current.
    """
    pool = eligible_devices(devices, direction, order_ids, hidden_ids)
    if not pool:
        return None
    ids = [d.id for d in pool]
    if current_id not in ids:
        # Current device isn't part of the cycle: start at the first entry.
        return pool[0]
    if len(pool) == 1:
        return None
    idx = (ids.index(current_id) + step) % len(pool)
    return pool[idx]
