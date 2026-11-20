"""Unit tests for `UserConfig` and `UserConfigStore`."""

from __future__ import annotations

from pathlib import Path

from open_escl_client.config.user_config import (
    UserConfig,
    UserConfigStore,
    default_config_dir,
    normalize_save_format,
)


def test_default_config_dir_per_platform(monkeypatch) -> None:
    mac_dir = default_config_dir("darwin")
    assert mac_dir.parts[-3:] == ("Library", "Application Support", "open-escl-client")

    monkeypatch.setenv("APPDATA", "/tmp/fake_appdata")
    win_dir = default_config_dir("win32")
    assert win_dir == Path("/tmp/fake_appdata") / "open-escl-client"


def test_user_config_store_roundtrip_and_validation(tmp_path: Path) -> None:
    cfg_file = tmp_path / "nested" / "config.json"
    store = UserConfigStore(config_path=cfg_file)

    # Missing file returns defaults
    default_cfg = store.load()
    assert default_cfg.save_format == "PDF"
    assert default_cfg.naming_rule == "Scan_{date}_{time}_{seq:03d}"
    assert default_cfg.jpeg_quality == 85
    assert default_cfg.preview_dpi == 100
    assert default_cfg.bleed_through_enabled is True
    assert default_cfg.bleed_through_whiten_background is True

    # Save custom config and reload
    custom = UserConfig(
        save_directory=str(tmp_path / "MyScans"),
        naming_rule="Receipt_{yyyy}-{mm}-{dd}_{seq:04d}",
        save_format="JPEG",
        resolution_dpi=600,
        preview_dpi=150,
        jpeg_quality=92,
        multipage_pdf=True,
        last_scanner_url="http://192.168.100.13:80/eSCL",
        input_source="ADF (片面)",
        paper_size_code="A4_P",
        auto_crop_enabled=False,
        multi_document_enabled=False,
        auto_orientation_enabled=False,
        detection_preset="standard_sheet",
        detection_sensitivity=3,
        aspect_ratio_preset="iso_a_b",
        lock_crop_aspect_ratio=True,
        crop_margin_mm=-1.5,
        bleed_through_enabled=False,
        bleed_through_strength=0.85,
        bleed_through_whiten_background=False,
    )
    saved_path = store.save(custom)
    assert saved_path.is_file()

    reloaded = store.load()
    assert reloaded.save_directory == str(tmp_path / "MyScans")
    assert reloaded.naming_rule == "Receipt_{yyyy}-{mm}-{dd}_{seq:04d}"
    assert reloaded.save_format == "JPEG"
    assert reloaded.resolution_dpi == 600
    assert reloaded.preview_dpi == 150
    assert reloaded.jpeg_quality == 92
    assert reloaded.multipage_pdf is True
    assert reloaded.last_scanner_url == "http://192.168.100.13:80/eSCL"
    assert reloaded.input_source == "ADF (片面)"
    assert reloaded.paper_size_code == "A4_P"
    assert reloaded.auto_crop_enabled is False
    assert reloaded.multi_document_enabled is False
    assert reloaded.auto_orientation_enabled is False
    assert reloaded.detection_preset == "standard_sheet"
    assert reloaded.detection_sensitivity == 3
    assert reloaded.aspect_ratio_preset == "iso_a_b"
    assert reloaded.lock_crop_aspect_ratio is True
    assert reloaded.crop_margin_mm == -1.5
    assert reloaded.bleed_through_enabled is False
    assert reloaded.bleed_through_strength == 0.85
    assert reloaded.bleed_through_whiten_background is False
    assert reloaded.resolved_save_directory() == tmp_path / "MyScans"

    # Reset restores all defaults on disk
    reset_cfg = store.reset()
    assert reset_cfg.naming_rule == "Scan_{date}_{time}_{seq:03d}"
    assert reset_cfg.preview_dpi == 100
    assert reset_cfg.bleed_through_enabled is True
    assert reset_cfg.bleed_through_whiten_background is True
    assert store.load() == reset_cfg


def test_user_config_store_handles_corrupt_json_and_clamps_values(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text("{not valid json", encoding="utf-8")
    store = UserConfigStore(config_path=cfg_file)
    assert store.load().save_format == "PDF"

    # Clamping out-of-range values
    clamped = UserConfig.from_dict(
        {
            "save_format": "jpg",
            "jpeg_quality": 200,
            "resolution_dpi": 99999,
            "preview_dpi": 9999,
            "detection_preset": "invalid_preset",
            "detection_sensitivity": 99,
            "aspect_ratio_preset": "unknown_ratio",
            "crop_margin_mm": -99.0,
            "bleed_through_strength": 5.0,
        }
    )
    assert clamped.save_format == "JPEG"
    assert clamped.jpeg_quality == 100
    assert clamped.resolution_dpi == 2400
    assert clamped.preview_dpi == 600
    assert clamped.detection_preset == "auto"
    assert clamped.detection_sensitivity == 5
    assert clamped.aspect_ratio_preset == "auto"
    assert clamped.crop_margin_mm == -10.0
    assert clamped.bleed_through_strength == 1.0
    assert normalize_save_format("unknown") == "PDF"

    # Legacy partial dict safely fills defaults for new fields
    legacy = UserConfig.from_dict({"save_format": "PNG", "resolution_dpi": 200})
    assert legacy.save_format == "PNG"
    assert legacy.resolution_dpi == 200
    assert legacy.naming_rule == "Scan_{date}_{time}_{seq:03d}"
    assert legacy.bleed_through_enabled is True
    assert legacy.bleed_through_whiten_background is True
