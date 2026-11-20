"""Persistent user configuration store with macOS and Windows path abstractions."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_DIR_NAME = "open-escl-client"
DEFAULT_NAMING_RULE = "Scan_{date}_{time}_{seq:03d}"
VALID_SAVE_FORMATS: tuple[str, ...] = ("PDF", "JPEG", "PNG", "TIFF", "WebP", "BMP", "GIF")
_SAVE_FORMAT_ALIASES: dict[str, str] = {
    "PDF": "PDF",
    "JPEG": "JPEG",
    "JPG": "JPEG",
    "PNG": "PNG",
    "TIFF": "TIFF",
    "TIF": "TIFF",
    "WEBP": "WebP",
    "BMP": "BMP",
    "GIF": "GIF",
}
VALID_DETECTION_PRESETS: tuple[str, ...] = (
    "auto",
    "receipt_multi",
    "standard_sheet",
    "full_page",
)
VALID_ASPECT_RATIO_PRESETS: tuple[str, ...] = (
    "auto",
    "iso_a_b",
    "postcard",
    "l_photo",
    "kg_photo",
    "2l_photo",
    "business_card",
    "free",
)


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
    """Normalize a save format or extension string to one of `VALID_SAVE_FORMATS`."""
    upper = str(fmt or "").strip().lstrip(".").upper()
    return _SAVE_FORMAT_ALIASES.get(upper, "PDF")


def _normalize_choice(raw: object, valid: tuple[str, ...], default: str) -> str:
    cleaned = str(raw or "").strip().lower()
    return cleaned if cleaned in valid else default


def _clamp_int(raw: object, default: int, min_val: int, max_val: int) -> int:
    try:
        return max(min_val, min(max_val, int(raw)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _clamp_float(raw: object, default: float, min_val: float, max_val: float) -> float:
    try:
        return max(min_val, min(max_val, float(raw)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class UserConfig:
    """Persisted user preferences for scanning, cropping, enhancement, and saving."""

    save_directory: str = "~/Pictures/Scans"
    naming_rule: str = DEFAULT_NAMING_RULE
    save_format: str = "PDF"
    resolution_dpi: int = 300
    preview_dpi: int = 100
    jpeg_quality: int = 85
    multipage_pdf: bool = False
    last_scanner_url: str = ""
    input_source: str = ""
    paper_size_code: str = ""
    auto_crop_enabled: bool = True
    multi_document_enabled: bool = True
    auto_orientation_enabled: bool = True
    detection_preset: str = "auto"
    detection_sensitivity: int = 0
    aspect_ratio_preset: str = "auto"
    lock_crop_aspect_ratio: bool = False
    crop_margin_mm: float = 0.0
    bleed_through_enabled: bool = True
    bleed_through_strength: float = 0.6
    bleed_through_whiten_background: bool = True
    expanded_sidebar_sections: dict[str, bool] = field(default_factory=dict)

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
    def defaults(cls) -> UserConfig:
        """Return a fresh `UserConfig` instance initialized to default values."""
        return cls()

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> UserConfig:
        """Construct a validated `UserConfig` from a parsed dictionary."""
        save_dir = (
            str(data.get("save_directory") or "~/Pictures/Scans").strip() or "~/Pictures/Scans"
        )
        naming_rule = (
            str(data.get("naming_rule") or DEFAULT_NAMING_RULE).strip() or DEFAULT_NAMING_RULE
        )
        save_format = normalize_save_format(str(data.get("save_format") or "PDF"))

        resolution_dpi = _clamp_int(data.get("resolution_dpi", 300), 300, 50, 2400)
        preview_dpi = _clamp_int(data.get("preview_dpi", 100), 100, 50, 600)
        jpeg_quality = _clamp_int(data.get("jpeg_quality", 85), 85, 1, 100)

        multipage_pdf = bool(data.get("multipage_pdf", False))
        last_scanner_url = str(data.get("last_scanner_url") or "").strip()
        input_source = str(data.get("input_source") or "").strip()
        paper_size_code = str(data.get("paper_size_code") or "").strip()

        auto_crop_enabled = bool(data.get("auto_crop_enabled", True))
        multi_document_enabled = bool(data.get("multi_document_enabled", True))
        auto_orientation_enabled = bool(data.get("auto_orientation_enabled", True))
        detection_preset = _normalize_choice(
            data.get("detection_preset"), VALID_DETECTION_PRESETS, "auto"
        )
        detection_sensitivity = _clamp_int(data.get("detection_sensitivity", 0), 0, -5, 5)
        aspect_ratio_preset = _normalize_choice(
            data.get("aspect_ratio_preset"), VALID_ASPECT_RATIO_PRESETS, "auto"
        )
        lock_crop_aspect_ratio = bool(data.get("lock_crop_aspect_ratio", False))
        crop_margin_mm = round(_clamp_float(data.get("crop_margin_mm", 0.0), 0.0, -10.0, 10.0), 2)

        bleed_through_enabled = bool(data.get("bleed_through_enabled", True))
        bleed_through_strength = round(
            _clamp_float(data.get("bleed_through_strength", 0.6), 0.6, 0.0, 1.0), 2
        )
        bleed_through_whiten_background = bool(
            data.get("bleed_through_whiten_background", True)
        )
        raw_sections = data.get("expanded_sidebar_sections")
        expanded_sidebar_sections = (
            {str(k): bool(v) for k, v in raw_sections.items()}
            if isinstance(raw_sections, dict)
            else {}
        )

        return cls(
            save_directory=save_dir,
            naming_rule=naming_rule,
            save_format=save_format,
            resolution_dpi=resolution_dpi,
            preview_dpi=preview_dpi,
            jpeg_quality=jpeg_quality,
            multipage_pdf=multipage_pdf,
            last_scanner_url=last_scanner_url,
            input_source=input_source,
            paper_size_code=paper_size_code,
            auto_crop_enabled=auto_crop_enabled,
            multi_document_enabled=multi_document_enabled,
            auto_orientation_enabled=auto_orientation_enabled,
            detection_preset=detection_preset,
            detection_sensitivity=detection_sensitivity,
            aspect_ratio_preset=aspect_ratio_preset,
            lock_crop_aspect_ratio=lock_crop_aspect_ratio,
            crop_margin_mm=crop_margin_mm,
            bleed_through_enabled=bleed_through_enabled,
            bleed_through_strength=bleed_through_strength,
            bleed_through_whiten_background=bleed_through_whiten_background,
            expanded_sidebar_sections=expanded_sidebar_sections,
        )


class UserConfigStore:
    """Loads and saves `UserConfig` as JSON in the OS-standard user config directory."""

    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = (
            config_path if config_path is not None else (default_config_dir() / "config.json")
        )

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

    def reset(self) -> UserConfig:
        """Reset persisted configuration to factory defaults and write it to disk."""
        defaults = UserConfig.defaults()
        self.save(defaults)
        return defaults
        return self.config_path
