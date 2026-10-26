"""Typed data models shared by every layer.

Nothing in here touches Windows, Qt or the file system.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

RENDER = "render"
CAPTURE = "capture"
DIRECTIONS = (RENDER, CAPTURE)

STATE_ACTIVE = "active"
STATE_DISABLED = "disabled"
STATE_UNPLUGGED = "unplugged"
STATE_NOT_PRESENT = "not_present"

ROLE_CONSOLE = "console"
ROLE_MULTIMEDIA = "multimedia"
ROLE_COMMUNICATIONS = "communications"
ALL_ROLES = (ROLE_CONSOLE, ROLE_MULTIMEDIA, ROLE_COMMUNICATIONS)
# The role the UI treats as "the" default when roles disagree. Windows' own
# Sound panel sets console+multimedia together when you pick "Set Default",
# so console is the least surprising primary role.
PRIMARY_ROLE = ROLE_CONSOLE

# Hotkey actions
ACTION_CYCLE_OUTPUT = "cycle_output"
ACTION_CYCLE_INPUT = "cycle_input"
ACTION_SET_OUTPUT = "set_output"
ACTION_SET_INPUT = "set_input"
ACTION_ACTIVATE_PROFILE = "activate_profile"
ACTION_TOGGLE_MIC_MUTE = "toggle_mic_mute"
ACTION_TOGGLE_OUTPUT_MUTE = "toggle_output_mute"
ACTION_VOLUME_UP = "volume_up"
ACTION_VOLUME_DOWN = "volume_down"
ACTION_SHOW_WINDOW = "show_window"
ALL_ACTIONS = (
    ACTION_CYCLE_OUTPUT,
    ACTION_CYCLE_INPUT,
    ACTION_SET_OUTPUT,
    ACTION_SET_INPUT,
    ACTION_ACTIVATE_PROFILE,
    ACTION_TOGGLE_MIC_MUTE,
    ACTION_TOGGLE_OUTPUT_MUTE,
    ACTION_VOLUME_UP,
    ACTION_VOLUME_DOWN,
    ACTION_SHOW_WINDOW,
)
ACTIONS_WITH_TARGET = (ACTION_SET_OUTPUT, ACTION_SET_INPUT, ACTION_ACTIVATE_PROFILE)

ACTION_LABELS = {
    ACTION_CYCLE_OUTPUT: "Cycle output device",
    ACTION_CYCLE_INPUT: "Cycle input device",
    ACTION_SET_OUTPUT: "Select specific output",
    ACTION_SET_INPUT: "Select specific input",
    ACTION_ACTIVATE_PROFILE: "Activate profile",
    ACTION_TOGGLE_MIC_MUTE: "Toggle microphone mute",
    ACTION_TOGGLE_OUTPUT_MUTE: "Toggle output mute",
    ACTION_VOLUME_UP: "Output volume up",
    ACTION_VOLUME_DOWN: "Output volume down",
    ACTION_SHOW_WINDOW: "Show / hide window",
}


@dataclass(frozen=True)
class AudioDevice:
    id: str
    name: str
    direction: str  # RENDER | CAPTURE
    state: str = STATE_ACTIVE
    is_default_console: bool = False
    is_default_multimedia: bool = False
    is_default_communications: bool = False
    description: str = ""  # e.g. "Realtek High Definition Audio" (adapter name)

    @property
    def is_active(self) -> bool:
        return self.state == STATE_ACTIVE

    @property
    def is_default(self) -> bool:
        """Default for the primary role."""
        return getattr(self, f"is_default_{PRIMARY_ROLE}")

    @property
    def is_any_default(self) -> bool:
        return self.is_default_console or self.is_default_multimedia or self.is_default_communications

    @property
    def default_roles(self) -> tuple[str, ...]:
        roles = []
        if self.is_default_console:
            roles.append(ROLE_CONSOLE)
        if self.is_default_multimedia:
            roles.append(ROLE_MULTIMEDIA)
        if self.is_default_communications:
            roles.append(ROLE_COMMUNICATIONS)
        return tuple(roles)

    def with_defaults(self, roles: tuple[str, ...]) -> "AudioDevice":
        return AudioDevice(
            id=self.id,
            name=self.name,
            direction=self.direction,
            state=self.state,
            is_default_console=ROLE_CONSOLE in roles,
            is_default_multimedia=ROLE_MULTIMEDIA in roles,
            is_default_communications=ROLE_COMMUNICATIONS in roles,
            description=self.description,
        )

    def with_state(self, state: str) -> "AudioDevice":
        return AudioDevice(
            id=self.id,
            name=self.name,
            direction=self.direction,
            state=state,
            is_default_console=self.is_default_console,
            is_default_multimedia=self.is_default_multimedia,
            is_default_communications=self.is_default_communications,
            description=self.description,
        )


@dataclass(frozen=True)
class AudioSession:
    """An application audio session on the default output device."""

    key: str  # stable-ish key (instance id or pid+name)
    pid: int
    name: str  # friendly process / display name
    volume: float  # 0..1
    muted: bool
    state: str = "active"  # active | inactive | expired
    is_system: bool = False


@dataclass
class AudioProfile:
    id: str
    name: str
    output_device_id: str | None = None
    input_device_id: str | None = None
    # Optional: route the Communications role (Discord/Teams/Zoom) elsewhere.
    comms_output_device_id: str | None = None
    comms_input_device_id: str | None = None
    hotkey_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "AudioProfile":
        return AudioProfile(
            id=str(d["id"]),
            name=str(d.get("name", d["id"])),
            output_device_id=d.get("output_device_id"),
            input_device_id=d.get("input_device_id"),
            comms_output_device_id=d.get("comms_output_device_id"),
            comms_input_device_id=d.get("comms_input_device_id"),
            hotkey_id=d.get("hotkey_id"),
        )

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.output_device_id,
                self.input_device_id,
                self.comms_output_device_id,
                self.comms_input_device_id,
            )
        )


@dataclass
class HotkeyBinding:
    id: str
    modifiers: list[str]  # subset of ctrl, alt, shift, win
    key: str  # "O", "1", "F5", "VOLUME_MUTE", ...
    action: str
    target_id: str | None = None
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "HotkeyBinding":
        return HotkeyBinding(
            id=str(d["id"]),
            modifiers=[str(m).lower() for m in d.get("modifiers", [])],
            key=str(d.get("key", "")).upper(),
            action=str(d.get("action", "")),
            target_id=d.get("target_id"),
            enabled=bool(d.get("enabled", True)),
        )

    @property
    def label(self) -> str:
        order = {"ctrl": 0, "alt": 1, "shift": 2, "win": 3}
        mods = sorted(set(self.modifiers), key=lambda m: order.get(m, 9))
        pretty = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
        return "+".join([pretty.get(m, m.title()) for m in mods] + [self.key.title() if len(self.key) > 1 else self.key])

    @property
    def signature(self) -> tuple[tuple[str, ...], str]:
        return (tuple(sorted(set(self.modifiers))), self.key.upper())


@dataclass
class AutoSwitchRule:
    enabled: bool = False
    priority_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any] | None) -> "AutoSwitchRule":
        d = d or {}
        return AutoSwitchRule(enabled=bool(d.get("enabled", False)), priority_ids=[str(x) for x in d.get("priority_ids", [])])


TOAST_POSITIONS = ("bottom-right", "bottom-left", "top-right", "top-left", "bottom-center", "top-center")


@dataclass
class AppConfig:
    schema_version: int = 1
    start_with_windows: bool = False
    start_minimized: bool = True
    default_roles_to_update: list[str] = field(default_factory=lambda: list(ALL_ROLES))
    # Overlay / toast
    toast_notifications: bool = True
    toast_position: str = "bottom-right"
    toast_duration_ms: int = 2200
    toast_on_external_changes: bool = True  # also show when Windows/other apps change the default
    # Audio feedback
    chime_on_switch: bool = True
    chime_volume: float = 0.4
    # Volume memory
    remember_device_volumes: bool = True
    volume_memory: dict[str, float] = field(default_factory=dict)
    volume_step: int = 4  # percent per wheel tick / hotkey press
    tray_wheel_volume: bool = True
    # Cycling
    cycle_output_ids: list[str] = field(default_factory=list)
    cycle_input_ids: list[str] = field(default_factory=list)
    # Auto switch
    auto_switch_output: AutoSwitchRule = field(default_factory=AutoSwitchRule)
    auto_switch_input: AutoSwitchRule = field(default_factory=AutoSwitchRule)
    # Device presentation
    device_aliases: dict[str, str] = field(default_factory=dict)
    hidden_device_ids: list[str] = field(default_factory=list)
    show_inactive_devices: bool = False
    # Bindings
    hotkeys: list[HotkeyBinding] = field(default_factory=list)
    profiles: list[AudioProfile] = field(default_factory=list)
    # Misc
    poll_interval_ms: int = 2500  # fallback polling when COM notifications are unavailable
    log_level: str = "INFO"

    # ---- helpers -------------------------------------------------------
    def profile_by_id(self, profile_id: str | None) -> AudioProfile | None:
        if profile_id is None:
            return None
        for p in self.profiles:
            if p.id == profile_id:
                return p
        return None

    def hotkey_by_id(self, hotkey_id: str | None) -> HotkeyBinding | None:
        if hotkey_id is None:
            return None
        for h in self.hotkeys:
            if h.id == hotkey_id:
                return h
        return None

    def display_name(self, device: AudioDevice) -> str:
        return self.device_aliases.get(device.id) or device.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "start_with_windows": self.start_with_windows,
            "start_minimized": self.start_minimized,
            "default_roles_to_update": list(self.default_roles_to_update),
            "toast_notifications": self.toast_notifications,
            "toast_position": self.toast_position,
            "toast_duration_ms": self.toast_duration_ms,
            "toast_on_external_changes": self.toast_on_external_changes,
            "chime_on_switch": self.chime_on_switch,
            "chime_volume": self.chime_volume,
            "remember_device_volumes": self.remember_device_volumes,
            "volume_memory": dict(self.volume_memory),
            "volume_step": self.volume_step,
            "tray_wheel_volume": self.tray_wheel_volume,
            "cycle_output_ids": list(self.cycle_output_ids),
            "cycle_input_ids": list(self.cycle_input_ids),
            "auto_switch_output": self.auto_switch_output.to_dict(),
            "auto_switch_input": self.auto_switch_input.to_dict(),
            "device_aliases": dict(self.device_aliases),
            "hidden_device_ids": list(self.hidden_device_ids),
            "show_inactive_devices": self.show_inactive_devices,
            "hotkeys": [h.to_dict() for h in self.hotkeys],
            "profiles": [p.to_dict() for p in self.profiles],
            "poll_interval_ms": self.poll_interval_ms,
            "log_level": self.log_level,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "AppConfig":
        cfg = AppConfig()
        cfg.schema_version = int(d.get("schema_version", 1))
        cfg.start_with_windows = bool(d.get("start_with_windows", cfg.start_with_windows))
        cfg.start_minimized = bool(d.get("start_minimized", cfg.start_minimized))
        roles = [r for r in d.get("default_roles_to_update", cfg.default_roles_to_update) if r in ALL_ROLES]
        cfg.default_roles_to_update = roles or list(ALL_ROLES)
        cfg.toast_notifications = bool(d.get("toast_notifications", cfg.toast_notifications))
        pos = str(d.get("toast_position", cfg.toast_position))
        cfg.toast_position = pos if pos in TOAST_POSITIONS else "bottom-right"
        cfg.toast_duration_ms = max(500, int(d.get("toast_duration_ms", cfg.toast_duration_ms)))
        cfg.toast_on_external_changes = bool(d.get("toast_on_external_changes", cfg.toast_on_external_changes))
        cfg.chime_on_switch = bool(d.get("chime_on_switch", cfg.chime_on_switch))
        cfg.chime_volume = min(1.0, max(0.0, float(d.get("chime_volume", cfg.chime_volume))))
        cfg.remember_device_volumes = bool(d.get("remember_device_volumes", cfg.remember_device_volumes))
        cfg.volume_memory = {str(k): min(1.0, max(0.0, float(v))) for k, v in dict(d.get("volume_memory", {})).items()}
        cfg.volume_step = max(1, min(25, int(d.get("volume_step", cfg.volume_step))))
        cfg.tray_wheel_volume = bool(d.get("tray_wheel_volume", cfg.tray_wheel_volume))
        cfg.cycle_output_ids = [str(x) for x in d.get("cycle_output_ids", [])]
        cfg.cycle_input_ids = [str(x) for x in d.get("cycle_input_ids", [])]
        cfg.auto_switch_output = AutoSwitchRule.from_dict(d.get("auto_switch_output"))
        cfg.auto_switch_input = AutoSwitchRule.from_dict(d.get("auto_switch_input"))
        cfg.device_aliases = {str(k): str(v) for k, v in dict(d.get("device_aliases", {})).items() if str(v).strip()}
        cfg.hidden_device_ids = [str(x) for x in d.get("hidden_device_ids", [])]
        cfg.show_inactive_devices = bool(d.get("show_inactive_devices", cfg.show_inactive_devices))
        cfg.hotkeys = [HotkeyBinding.from_dict(h) for h in d.get("hotkeys", []) if isinstance(h, dict) and "id" in h]
        cfg.profiles = [AudioProfile.from_dict(p) for p in d.get("profiles", []) if isinstance(p, dict) and "id" in p]
        cfg.poll_interval_ms = max(500, int(d.get("poll_interval_ms", cfg.poll_interval_ms)))
        cfg.log_level = str(d.get("log_level", cfg.log_level)).upper()
        return cfg


def default_config() -> AppConfig:
    """Config used on first launch. Device IDs are filled in by the user; the
    hotkeys below work without any device configuration."""
    cfg = AppConfig()
    cfg.hotkeys = [
        HotkeyBinding("cycle-out", ["ctrl", "alt"], "O", ACTION_CYCLE_OUTPUT),
        HotkeyBinding("cycle-in", ["ctrl", "alt"], "I", ACTION_CYCLE_INPUT),
        HotkeyBinding("mic-mute", ["ctrl", "alt"], "M", ACTION_TOGGLE_MIC_MUTE),
        HotkeyBinding("profile-1", ["ctrl", "alt"], "1", ACTION_ACTIVATE_PROFILE, "gaming"),
        HotkeyBinding("profile-2", ["ctrl", "alt"], "2", ACTION_ACTIVATE_PROFILE, "desktop"),
    ]
    cfg.profiles = [
        AudioProfile("gaming", "Gaming", hotkey_id="profile-1"),
        AudioProfile("desktop", "Desktop", hotkey_id="profile-2"),
    ]
    return cfg
