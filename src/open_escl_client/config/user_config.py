"""Persistent user configuration store with macOS and Windows path abstractions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sys

APP_DIR_NAME = "open-escl-client"
VALID_SAVE_FORMATS: tuple[str, ...] = ("PDF", "JPEG", "PNG")


def default_config_dir(platform_name: str | None = None) -> Path:
    """Return the OS-standard user configuration directory (macOS, Windows, Linux)."""
    plat = (platform_name or sys.platform).lower()
    if plat == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    if plat.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / APP_DIR_NAME
        return Path.home() / "AppData" / "Roaming" / APP_DIR_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / APP_DIR_NAME
    return Path.home() / ".config" / APP_DIR_NAME


def default_scan_save_dir() -> Path:
    """Return the default directory where scanned documents are saved."""
    return Path.home() / "Pictures" / "Scans"


def normalize_save_format(fmt: str) -> str:
    """Normalize a save format string to one of `PDF`, `JPEG`, `PNG`."""
    upper = fmt.strip().upper()
    if upper == "JPG":
        return "JPEG"
    if upper in VALID_SAVE_FORMATS:
        return upper
    return "PDF"


@dataclass(slots=True)
class UserConfig:
    """Persisted user preferences for scanning and saving."""

    save_directory: str = "~/Pictures/Scans"
    naming_rule: str = "Scan_{date}_{seq:03d}"
    save_format: str = "PDF"
    resolution_dpi: int = 300
    jpeg_quality: int = 85
    multipage_pdf: bool = False
    last_scanner_url: str = ""

    def resolved_save_directory(self) -> Path:
        """Return `save_directory` with `~` expanded into an OS-native `Path`."""
        raw = self.save_directory.strip()
        if not raw:
            return default_scan_save_dir()
        return Path(raw).expanduser()

    def to_dict(self) -> dict[str, object]:
        """Serialize configuration fields into a JSON-compatible dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> UserConfig:
        """Construct a validated `UserConfig` from a parsed dictionary."""
        save_dir = str(data.get("save_directory") or "~/Pictures/Scans").strip() or "~/Pictures/Scans"
        naming_rule = str(data.get("naming_rule") or "Scan_{date}_{seq:03d}").strip() or "Scan_{date}_{seq:03d}"
        save_format = normalize_save_format(str(data.get("save_format") or "PDF"))

        try:
            resolution_dpi = max(50, min(2400, int(data.get("resolution_dpi", 300))))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            resolution_dpi = 300

        try:
            jpeg_quality = max(1, min(100, int(data.get("jpeg_quality", 85))))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            jpeg_quality = 85

        multipage_pdf = bool(data.get("multipage_pdf", False))
        last_scanner_url = str(data.get("last_scanner_url") or "").strip()

        return cls(
            save_directory=save_dir,
            naming_rule=naming_rule,
            save_format=save_format,
            resolution_dpi=resolution_dpi,
            jpeg_quality=jpeg_quality,
            multipage_pdf=multipage_pdf,
            last_scanner_url=last_scanner_url,
        )


class UserConfigStore:
    """Loads and saves `UserConfig` as JSON in the OS-standard user config directory."""

    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path if config_path is not None else (default_config_dir() / "config.json")

    def load(self) -> UserConfig:
        """Load configuration from disk, returning defaults if missing or unreadable."""
        if not self.config_path.is_file():
            return UserConfig()
        try:
            raw_text = self.config_path.read_text(encoding="utf-8")
            parsed = json.loads(raw_text)
            if not isinstance(parsed, dict):
                return UserConfig()
            return UserConfig.from_dict(parsed)
        except (OSError, json.JSONDecodeError):
            return UserConfig()

    def save(self, config: UserConfig) -> Path:
        """Persist `config` to disk as UTF-8 JSON, creating parent directories if needed."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(config.to_dict(), ensure_ascii=False, indent=2) + "\n"
        tmp_path = self.config_path.with_suffix(".tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(self.config_path)
        return self.config_path
