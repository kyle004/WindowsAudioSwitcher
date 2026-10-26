"""Default-endpoint setter built on the undocumented IPolicyConfig COM interface.

Windows exposes no public API for changing the default audio endpoint. Every
switcher (SoundSwitch, AudioSwitcher, NirSoft SoundVolumeView, EarTrumpet)
uses `IPolicyConfig::SetDefaultEndpoint` from `audiosrv`/`mmres`. The GUIDs and
vtable layouts below are copied from those long-running, widely deployed
implementations. They are stable across Windows 7, 8, 10 and 11 so far, but
Microsoft could change them in any update. `DefaultDeviceSetter` therefore:

* tries three known interface variants in order and remembers which worked,
* never guesses: a QueryInterface failure moves to the next variant,
* raises `BackendError` with a readable message when nothing works, so the UI
  can suggest the SoundVolumeView fallback.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from ctypes import POINTER, c_int64, c_void_p
from ctypes.wintypes import DWORD, INT, LPCWSTR

import comtypes
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

from ..device_service import BackendError
from ..models import ROLE_COMMUNICATIONS, ROLE_CONSOLE, ROLE_MULTIMEDIA

log = logging.getLogger(__name__)

ROLE_TO_EROLE = {ROLE_CONSOLE: 0, ROLE_MULTIMEDIA: 1, ROLE_COMMUNICATIONS: 2}

CLSID_POLICY_CONFIG_CLIENT = GUID("{870af99c-171d-4f9e-af0d-e63df40c2bc9}")
CLSID_POLICY_CONFIG_VISTA_CLIENT = GUID("{294935CE-F637-4E7C-A41B-AB255460B862}")


def _common_head() -> list:
    return [
        COMMETHOD([], HRESULT, "GetMixFormat", (["in"], LPCWSTR, "pszDeviceName"), (["out"], POINTER(c_void_p), "ppFormat")),
        COMMETHOD([], HRESULT, "GetDeviceFormat", (["in"], LPCWSTR, "pszDeviceName"), (["in"], INT, "bDefault"), (["out"], POINTER(c_void_p), "ppFormat")),
    ]


def _common_tail() -> list:
    return [
        COMMETHOD([], HRESULT, "SetDeviceFormat", (["in"], LPCWSTR, "pszDeviceName"), (["in"], c_void_p, "pEndpointFormat"), (["in"], c_void_p, "pMixFormat")),
        COMMETHOD([], HRESULT, "GetProcessingPeriod", (["in"], LPCWSTR, "pszDeviceName"), (["in"], INT, "bDefault"), (["out"], POINTER(c_int64), "pmftDefaultPeriod"), (["out"], POINTER(c_int64), "pmftMinimumPeriod")),
        COMMETHOD([], HRESULT, "SetProcessingPeriod", (["in"], LPCWSTR, "pszDeviceName"), (["in"], POINTER(c_int64), "pmftPeriod")),
        COMMETHOD([], HRESULT, "GetShareMode", (["in"], LPCWSTR, "pszDeviceName"), (["out"], c_void_p, "pMode")),
        COMMETHOD([], HRESULT, "SetShareMode", (["in"], LPCWSTR, "pszDeviceName"), (["in"], c_void_p, "pMode")),
        COMMETHOD([], HRESULT, "GetPropertyValue", (["in"], LPCWSTR, "pszDeviceName"), (["in"], c_void_p, "key"), (["out"], c_void_p, "pv")),
        COMMETHOD([], HRESULT, "SetPropertyValue", (["in"], LPCWSTR, "pszDeviceName"), (["in"], c_void_p, "key"), (["in"], c_void_p, "pv")),
        COMMETHOD([], HRESULT, "SetDefaultEndpoint", (["in"], LPCWSTR, "wszDeviceId"), (["in"], DWORD, "eRole")),
        COMMETHOD([], HRESULT, "SetEndpointVisibility", (["in"], LPCWSTR, "wszDeviceId"), (["in"], INT, "bVisible")),
    ]


class IPolicyConfig(IUnknown):
    """Windows 7+ layout (12 methods). Known-good on Windows 10 and 11."""

    _iid_ = GUID("{f8679f50-850a-41e1-895d-c19f0fe5e0b5}")
    _methods_ = _common_head() + [
        COMMETHOD([], HRESULT, "ResetDeviceFormat", (["in"], LPCWSTR, "pszDeviceName")),
    ] + _common_tail()


class IPolicyConfigWin10(IUnknown):
    """Same layout as IPolicyConfig but the IID Windows 10 1607+ registers first."""

    _iid_ = GUID("{CA286FC3-91FD-42C3-8E9B-CAAFA66242E3}")
    _methods_ = _common_head() + [
        COMMETHOD([], HRESULT, "ResetDeviceFormat", (["in"], LPCWSTR, "pszDeviceName")),
    ] + _common_tail()


class IPolicyConfigVista(IUnknown):
    """Vista layout (11 methods, no ResetDeviceFormat). Still served on Windows 10/11."""

    _iid_ = GUID("{568b9108-44bf-40b4-9006-86afe5b5a620}")
    _methods_ = _common_head() + _common_tail()


VARIANTS: tuple[tuple[str, type, GUID], ...] = (
    ("IPolicyConfig (Win7+)", IPolicyConfig, CLSID_POLICY_CONFIG_CLIENT),
    ("IPolicyConfig (Win10 IID)", IPolicyConfigWin10, CLSID_POLICY_CONFIG_CLIENT),
    ("IPolicyConfigVista", IPolicyConfigVista, CLSID_POLICY_CONFIG_VISTA_CLIENT),
)


class DefaultDeviceSetter:
    """Facade: `set_default(device_id, roles)`. Picks a working IPolicyConfig variant lazily."""

    def __init__(self) -> None:
        self._client = None
        self.variant_name: str | None = None
        self.last_error: str | None = None
        self._svv = SoundVolumeViewSetter.locate()

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        errors = []
        for name, iface, clsid in VARIANTS:
            try:
                client = comtypes.CoCreateInstance(clsid, interface=iface, clsctx=comtypes.CLSCTX_ALL)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{name}: {exc}")
                continue
            self._client = client
            self.variant_name = name
            log.info("IPolicyConfig variant in use: %s", name)
            return client
        self.last_error = "; ".join(errors)
        raise BackendError(
            "IPolicyConfig is not available on this Windows build. "
            + ("SoundVolumeView fallback will be used." if self._svv else "Install NirSoft SoundVolumeView next to AudioSwitch to enable the fallback.")
        )

    def set_default(self, device_id: str, roles: tuple[str, ...]) -> None:
        try:
            client = self._ensure_client()
        except BackendError:
            if self._svv:
                self._svv.set_default(device_id, roles)
                return
            raise
        for role in roles:
            erole = ROLE_TO_EROLE[role]
            try:
                hr = client.SetDefaultEndpoint(device_id, erole)
            except Exception as exc:  # noqa: BLE001
                # Variant may be wrong for this build; forget it so the next call tries again.
                self._client = None
                raise BackendError(f"SetDefaultEndpoint({role}) failed via {self.variant_name}: {exc}") from exc
            if hr not in (None, 0):
                raise BackendError(f"SetDefaultEndpoint({role}) returned HRESULT 0x{hr & 0xFFFFFFFF:08X}")

    def describe(self) -> str:
        if self.variant_name:
            return f"native {self.variant_name}"
        if self._svv:
            return f"SoundVolumeView at {self._svv.exe}"
        return "not yet initialised"


class SoundVolumeViewSetter:
    """Optional fallback using NirSoft SoundVolumeView.exe (freeware; must be
    downloaded by the user, it is NOT bundled). Place it next to the exe or on PATH."""

    def __init__(self, exe: str):
        self.exe = exe

    @classmethod
    def locate(cls) -> "SoundVolumeViewSetter | None":
        candidates = [
            os.environ.get("AUDIOSWITCH_SVV"),
            shutil.which("SoundVolumeView.exe"),
            os.path.join(os.path.dirname(os.path.abspath(__import__("sys").argv[0])), "SoundVolumeView.exe"),
        ]
        for c in candidates:
            if c and os.path.isfile(c):
                return cls(c)
        return None

    def set_default(self, device_id: str, roles: tuple[str, ...]) -> None:
        # SoundVolumeView: /SetDefault <Name or ID> <0|1|2|all>
        for role in roles:
            code = str(ROLE_TO_EROLE[role])
            try:
                subprocess.run([self.exe, "/SetDefault", device_id, code], check=True, timeout=10, creationflags=0x08000000)
            except Exception as exc:  # noqa: BLE001
                raise BackendError(f"SoundVolumeView failed to set {role}: {exc}") from exc
