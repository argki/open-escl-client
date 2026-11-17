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
    assert default_cfg.naming_rule == "Scan_{date}_{seq:03d}"
    assert default_cfg.jpeg_quality == 85

    # Save custom config and reload
    custom = UserConfig(
        save_directory=str(tmp_path / "MyScans"),
        naming_rule="Receipt_{yyyy}-{mm}-{dd}_{seq:04d}",
        save_format="JPEG",
        resolution_dpi=600,
        jpeg_quality=92,
        multipage_pdf=True,
        last_scanner_url="http://192.168.100.13:80/eSCL",
    )
    saved_path = store.save(custom)
    assert saved_path.is_file()

    reloaded = store.load()
    assert reloaded.save_directory == str(tmp_path / "MyScans")
    assert reloaded.naming_rule == "Receipt_{yyyy}-{mm}-{dd}_{seq:04d}"
    assert reloaded.save_format == "JPEG"
    assert reloaded.resolution_dpi == 600
    assert reloaded.jpeg_quality == 92
    assert reloaded.multipage_pdf is True
    assert reloaded.last_scanner_url == "http://192.168.100.13:80/eSCL"
    assert reloaded.resolved_save_directory() == tmp_path / "MyScans"


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
        }
    )
    assert clamped.save_format == "JPEG"
    assert clamped.jpeg_quality == 100
    assert clamped.resolution_dpi == 2400
    assert normalize_save_format("unknown") == "PDF"
