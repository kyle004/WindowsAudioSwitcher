"""Pure key-name <-> virtual-key / modifier parsing. Platform independent, unit tested."""
from __future__ import annotations

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

MODIFIER_FLAGS = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL, "shift": MOD_SHIFT, "win": MOD_WIN, "meta": MOD_WIN, "super": MOD_WIN}

_NAMED_KEYS = {
    "BACKSPACE": 0x08, "TAB": 0x09, "ENTER": 0x0D, "RETURN": 0x0D, "PAUSE": 0x13, "CAPSLOCK": 0x14, "ESC": 0x1B, "ESCAPE": 0x1B,
    "SPACE": 0x20, "PAGEUP": 0x21, "PAGEDOWN": 0x22, "END": 0x23, "HOME": 0x24,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28, "PRINTSCREEN": 0x2C, "INSERT": 0x2D, "DELETE": 0x2E,
    "NUMPAD0": 0x60, "NUMPAD1": 0x61, "NUMPAD2": 0x62, "NUMPAD3": 0x63, "NUMPAD4": 0x64, "NUMPAD5": 0x65,
    "NUMPAD6": 0x66, "NUMPAD7": 0x67, "NUMPAD8": 0x68, "NUMPAD9": 0x69,
    "MULTIPLY": 0x6A, "ADD": 0x6B, "SUBTRACT": 0x6D, "DECIMAL": 0x6E, "DIVIDE": 0x6F,
    "NUMLOCK": 0x90, "SCROLLLOCK": 0x91,
    "VOLUME_MUTE": 0xAD, "VOLUME_DOWN": 0xAE, "VOLUME_UP": 0xAF,
    "MEDIA_NEXT": 0xB0, "MEDIA_PREV": 0xB1, "MEDIA_STOP": 0xB2, "MEDIA_PLAY_PAUSE": 0xB3,
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}
for _i in range(1, 25):
    _NAMED_KEYS[f"F{_i}"] = 0x6F + _i

KEY_CHOICES = [chr(c) for c in range(ord("A"), ord("Z") + 1)] + [str(d) for d in range(10)] + sorted(
    (k for k in _NAMED_KEYS if len(k) > 1), key=lambda k: (not k.startswith("F"), len(k), k)
)


def modifiers_to_flags(modifiers: list[str], no_repeat: bool = True) -> int:
    flags = 0
    for m in modifiers:
        flags |= MODIFIER_FLAGS.get(m.lower(), 0)
    if no_repeat:
        flags |= MOD_NOREPEAT
    return flags


def key_to_vk(key: str) -> int | None:
    k = (key or "").strip().upper()
    if not k:
        return None
    if len(k) == 1 and (k.isalpha() or k.isdigit()):
        return ord(k)
    return _NAMED_KEYS.get(k)


def vk_to_key(vk: int) -> str | None:
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    for name, code in _NAMED_KEYS.items():
        if code == vk and len(name) > 1:
            return name
    return None


def normalize_modifiers(modifiers: list[str]) -> list[str]:
    canon = {"control": "ctrl", "meta": "win", "super": "win"}
    seen: list[str] = []
    for m in modifiers:
        m = canon.get(m.lower(), m.lower())
        if m in MODIFIER_FLAGS and m not in seen:
            seen.append(m)
    order = {"ctrl": 0, "alt": 1, "shift": 2, "win": 3}
    return sorted(seen, key=lambda m: order[m])


def validate_binding(modifiers: list[str], key: str) -> str | None:
    """Return an error string, or None when the combination is registerable."""
    if key_to_vk(key) is None:
        return f"Unknown key '{key}'."
    mods = normalize_modifiers(modifiers)
    vk = key_to_vk(key)
    media = vk is not None and 0xAD <= vk <= 0xB3
    if not mods and not media and not (vk is not None and 0x70 <= vk <= 0x87):
        return "Use at least one modifier (Ctrl, Alt, Shift or Win), or a function/media key."
    return None
