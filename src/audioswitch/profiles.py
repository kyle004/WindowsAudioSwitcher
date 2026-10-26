"""Profile validation and planning (no side effects)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import (
    ALL_ROLES,
    CAPTURE,
    RENDER,
    ROLE_COMMUNICATIONS,
    AudioDevice,
    AudioProfile,
)


@dataclass
class SwitchStep:
    direction: str
    device_id: str
    roles: tuple[str, ...]


@dataclass
class ProfilePlan:
    steps: list[SwitchStep] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_noop(self) -> bool:
        return not self.steps


def _find(devices: list[AudioDevice], device_id: str | None, direction: str) -> AudioDevice | None:
    if not device_id:
        return None
    for d in devices:
        if d.id == device_id and d.direction == direction:
            return d
    return None


def plan_profile(
    profile: AudioProfile,
    devices: list[AudioDevice],
    roles_to_update: list[str],
    name_of=lambda d: d.name,
) -> ProfilePlan:
    """Turn a profile into concrete switch steps.

    Main device gets `roles_to_update`; if a comms device is set it takes the
    communications role and the main device loses it.
    """
    plan = ProfilePlan()
    roles = tuple(r for r in roles_to_update if r in ALL_ROLES) or tuple(ALL_ROLES)

    for direction, main_id, comms_id, label in (
        (RENDER, profile.output_device_id, profile.comms_output_device_id, "Output"),
        (CAPTURE, profile.input_device_id, profile.comms_input_device_id, "Input"),
    ):
        main = _find(devices, main_id, direction)
        comms = _find(devices, comms_id, direction)
        if main_id and main is None:
            plan.warnings.append(f"{label} device for profile '{profile.name}' is not connected.")
        elif main is not None and not main.is_active:
            plan.warnings.append(f"{label} device '{name_of(main)}' is {main.state}; skipped.")
            main = None
        if comms_id and comms is None:
            plan.warnings.append(f"Communications {label.lower()} for profile '{profile.name}' is not connected.")
        elif comms is not None and not comms.is_active:
            plan.warnings.append(f"Communications {label.lower()} '{name_of(comms)}' is {comms.state}; skipped.")
            comms = None

        main_roles = roles
        if comms is not None and comms.id != (main.id if main else None):
            main_roles = tuple(r for r in roles if r != ROLE_COMMUNICATIONS)
            plan.steps.append(SwitchStep(direction, comms.id, (ROLE_COMMUNICATIONS,)))
        if main is not None and main_roles:
            plan.steps.insert(len(plan.steps) - (1 if comms is not None and comms.id != main.id else 0), SwitchStep(direction, main.id, main_roles))
    return plan


def validate_profile(profile: AudioProfile, existing: list[AudioProfile]) -> list[str]:
    errors: list[str] = []
    if not profile.id.strip():
        errors.append("Profile id is required.")
    if not profile.name.strip():
        errors.append("Profile name is required.")
    if profile.is_empty:
        errors.append("Profile must set at least one device.")
    for other in existing:
        if other is profile:
            continue
        if other.id == profile.id:
            errors.append(f"Another profile already uses id '{profile.id}'.")
        if other.name.strip().lower() == profile.name.strip().lower():
            errors.append(f"Another profile is already named '{profile.name}'.")
    return errors
