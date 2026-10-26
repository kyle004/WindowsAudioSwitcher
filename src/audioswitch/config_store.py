"""Load, validate, migrate and atomically persist the JSON config."""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import APP_NAME
from .models import AppConfig, default_config

log = logging.getLogger(__name__)

CURRENT_SCHEMA = 1


def app_data_dir() -> Path:
    override = os.environ.get("AUDIOSWITCH_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME


def config_path() -> Path:
    return app_data_dir() / "config.json"


def log_path() -> Path:
    return app_data_dir() / "audioswitch.log"


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Upgrade older schemas in place. Keep this additive and boring."""
    version = int(data.get("schema_version", 1))
    if version < 1:
        version = 1
    # Future migrations go here: if version == 1: ... version = 2
    data["schema_version"] = CURRENT_SCHEMA
    return data


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class ConfigStore:
    def __init__(self, path: Path | None = None):
        self.path = path or config_path()

    def load(self) -> AppConfig:
        if not self.path.exists():
            cfg = default_config()
            try:
                self.save(cfg)
            except OSError as exc:
                log.warning("Could not write initial config: %s", exc)
            return cfg
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("config root must be an object")
        except (OSError, ValueError) as exc:
            log.error("Config unreadable (%s); backing it up and using defaults", exc)
            self._backup_corrupt()
            return default_config()
        raw = migrate(raw)
        return AppConfig.from_dict(raw)

    def save(self, cfg: AppConfig) -> None:
        cfg.schema_version = CURRENT_SCHEMA
        atomic_write_text(self.path, json.dumps(cfg.to_dict(), indent=2, ensure_ascii=False) + "\n")

    def _backup_corrupt(self) -> None:
        try:
            backup = self.path.with_suffix(".corrupt.json")
            os.replace(self.path, backup)
            log.warning("Corrupt config moved to %s", backup)
        except OSError:
            pass

    # ---- import / export ----------------------------------------------
    @staticmethod
    def export_to(cfg: AppConfig, path: Path, include_volume_memory: bool = False) -> None:
        data = cfg.to_dict()
        if not include_volume_memory:
            data["volume_memory"] = {}
        atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    @staticmethod
    def import_from(path: Path) -> AppConfig:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Import file must contain a JSON object")
        return AppConfig.from_dict(migrate(raw))
