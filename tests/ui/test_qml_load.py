"""Tests for declarative QML loading and AppViewModel bindings."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication

from open_escl_client.escl.capabilities import parse_scanner_capabilities_xml
from open_escl_client.escl.discovery import DiscoveredScanner
from open_escl_client.ui.app import MAIN_QML_PATH, QML_DIR, create_qml_engine
from open_escl_client.ui.viewmodel import AppViewModel
from tests.escl.test_capabilities import BROTHER_CAPABILITIES_XML, GENERIC_A3_PROFILE_REF_XML


def _ensure_qapp() -> QGuiApplication:
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app  # type: ignore[return-value]


def test_main_qml_and_placement_preview_files_exist() -> None:
    assert MAIN_QML_PATH.is_file()
    assert (QML_DIR / "PaperPlacementPreview.qml").is_file()


def test_qml_engine_loads_main_window_and_components() -> None:
    _ensure_qapp()
    engine, vm = create_qml_engine()
    roots = engine.rootObjects()
    assert len(roots) == 1

    root = roots[0]
    assert root.property("title") == "Open eSCL Client"

    # Verify key declarative UI regions exist by objectName
    assert root.findChild(QObject, "scannerCombo") is not None
    assert root.findChild(QObject, "refreshScannerButton") is not None
    assert root.findChild(QObject, "manualHostField") is not None
    assert root.findChild(QObject, "connectManualButton") is not None
    assert root.findChild(QObject, "inputSourceCombo") is not None
    assert root.findChild(QObject, "paperSizeCombo") is not None
    assert root.findChild(QObject, "resolutionCombo") is not None
    assert root.findChild(QObject, "saveDirField") is not None
    assert root.findChild(QObject, "namingRuleField") is not None
    assert root.findChild(QObject, "saveFormatCombo") is not None
    assert root.findChild(QObject, "previewArea") is not None
    assert root.findChild(QObject, "actionToolbar") is not None
    assert root.findChild(QObject, "previewButton") is not None
    assert root.findChild(QObject, "scanAndSaveButton") is not None
    assert root.findChild(QObject, "placementGuidePopup") is not None
    assert root.findChild(QObject, "paperPlacementPreview") is not None
    assert root.findChild(QObject, "scannerBedRect") is not None
    assert root.findChild(QObject, "paperSheetRect") is not None

    for obj in roots:
        obj.deleteLater()
    engine.deleteLater()
    assert vm is not None


def test_hover_placement_guide_updates_preview_card_and_geometry() -> None:
    _ensure_qapp()
    a3_caps = parse_scanner_capabilities_xml(GENERIC_A3_PROFILE_REF_XML)
    mock_fetcher = MagicMock(return_value=a3_caps)
    vm = AppViewModel(capabilities_fetcher=mock_fetcher)
    vm.addManualScanner("192.168.1.60")

    engine, _ = create_qml_engine(vm)
    root = engine.rootObjects()[0]
    preview_card = root.findChild(QObject, "paperPlacementPreview")
    bed_rect = root.findChild(QObject, "scannerBedRect")
    paper_rect = root.findChild(QObject, "paperSheetRect")
    assert preview_card is not None
    assert bed_rect is not None
    assert paper_rect is not None

    # Verify the card and outer scanner bed rectangle have non-collapsed positive geometry
    assert float(preview_card.property("width")) >= 300.0
    assert float(preview_card.property("height")) >= 400.0
    bed_w = float(bed_rect.property("width"))
    bed_h = float(bed_rect.property("height"))
    assert bed_w > 100.0
    assert bed_h > 140.0

    # Default selected is A4 Portrait: paper rectangle is inside bed rectangle and portrait-shaped
    assert preview_card.property("orientation") == "portrait"
    paper_w_portrait = float(paper_rect.property("width"))
    paper_h_portrait = float(paper_rect.property("height"))
    assert 50.0 < paper_w_portrait < bed_w
    assert 70.0 < paper_h_portrait < bed_h
    assert paper_h_portrait > paper_w_portrait

    # Selecting A4 Landscape updates the inner paper rectangle to landscape-shaped inside the bed rectangle
    vm.selectedPaperSize = "A4 横置き (297 × 210 mm)"
    assert preview_card.property("orientation") == "landscape"
    assert preview_card.property("paperW") == 297.0
    assert preview_card.property("paperH") == 210.0
    paper_w_landscape = float(paper_rect.property("width"))
    paper_h_landscape = float(paper_rect.property("height"))
    assert paper_w_landscape > paper_h_landscape
    assert paper_w_landscape <= bed_w

    for obj in engine.rootObjects():
        obj.deleteLater()
    engine.deleteLater()


def test_viewmodel_properties_and_slots() -> None:
    vm = AppViewModel()
    assert "A4 縦置き (210 × 297 mm)" in vm.paperSizes
    assert "B5 (JIS) 縦置き (182 × 257 mm)" in vm.paperSizes
    assert "A5 縦置き (148 × 210 mm)" in vm.paperSizes
    assert "A5 横置き (210 × 148 mm)" in vm.paperSizes
    assert "ハガキ 縦置き (100 × 148 mm)" in vm.paperSizes
    assert "ハガキ 横置き (148 × 100 mm)" in vm.paperSizes
    assert len(vm.resolutions) >= 6
    assert vm.selectedResolution == 300
    assert vm.selectedSaveFormat == "PDF"

    vm.selectedPaperSize = "B5 (JIS) 縦置き (182 × 257 mm)"
    assert vm.selectedPaperSize == "B5 (JIS) 縦置き (182 × 257 mm)"

    vm.selectedResolution = 600
    assert vm.selectedResolution == 600

    vm.saveDirectory = "/tmp/scans"
    assert vm.saveDirectory == "/tmp/scans"

    vm.namingRule = "Receipt_{date}_{seq:04d}"
    assert vm.namingRule == "Receipt_{date}_{seq:04d}"

    vm.requestPreview()
    assert "プレビュー" in vm.statusMessage

    vm.requestScanAndSave()
    assert "600 dpi" in vm.statusMessage


def test_viewmodel_discovery_and_capabilities_binding() -> None:
    brother_scanner = DiscoveredScanner(
        name="Brother MFC-L2750DW series",
        host="BRW001122334455.local",
        port=80,
        scheme="http",
        addresses=("192.168.1.50",),
    )
    mock_discovery = MagicMock()
    mock_discovery.discover.return_value = [brother_scanner]

    brother_caps = parse_scanner_capabilities_xml(BROTHER_CAPABILITIES_XML)
    mock_fetcher = MagicMock(return_value=brother_caps)

    vm = AppViewModel(
        discovery_service=mock_discovery,
        capabilities_fetcher=mock_fetcher,
    )
    vm.refreshScanners()

    assert vm.selectedScanner == "Brother MFC-L2750DW series (192.168.1.50)"
    assert vm.inputSources == ["原稿台 (Platen)", "ADF (片面)", "ADF (両面)"]
    assert vm.resolutions == [100, 150, 200, 300, 600, 1200]
    assert "A4 縦置き (210 × 297 mm)" in vm.paperSizes
    assert "名刺 縦置き (55 × 91 mm)" in vm.paperSizes
    assert "名刺 横置き (91 × 55 mm)" in vm.paperSizes

    # Switch input source to ADF (Simplex) -> Legal becomes available, Business Card excluded
    vm.selectedInputSource = "ADF (片面)"
    assert vm.resolutions == [100, 300, 600]
    assert "リーガル (Legal) 縦置き (215.9 × 355.6 mm)" in vm.paperSizes
    assert "名刺 縦置き (55 × 91 mm)" not in vm.paperSizes


def test_viewmodel_a3_scanner_provides_both_a4_orientations_and_placement_guide() -> None:
    a3_caps = parse_scanner_capabilities_xml(GENERIC_A3_PROFILE_REF_XML)
    mock_fetcher = MagicMock(return_value=a3_caps)

    vm = AppViewModel(capabilities_fetcher=mock_fetcher)
    vm.addManualScanner("192.168.1.60")

    assert "A4 縦置き (210 × 297 mm)" in vm.paperSizes
    assert "A4 横置き (297 × 210 mm)" in vm.paperSizes

    guide_portrait = vm.getPlacementGuide("A4 縦置き (210 × 297 mm)")
    assert guide_portrait["orientation"] == "portrait"
    assert guide_portrait["paperWidthMm"] == 210.0
    assert guide_portrait["paperHeightMm"] == 297.0

    guide_landscape = vm.getPlacementGuide("A4 横置き (297 × 210 mm)")
    assert guide_landscape["orientation"] == "landscape"
    assert guide_landscape["paperWidthMm"] == 297.0
    assert guide_landscape["paperHeightMm"] == 210.0


def test_viewmodel_add_manual_scanner_by_ip() -> None:
    brother_caps = parse_scanner_capabilities_xml(BROTHER_CAPABILITIES_XML)
    mock_fetcher = MagicMock(return_value=brother_caps)

    vm = AppViewModel(capabilities_fetcher=mock_fetcher)
    vm.addManualScanner("192.168.1.50")

    mock_fetcher.assert_called_once_with("http://192.168.1.50:80/eSCL/ScannerCapabilities")
    assert "Brother MFC-L2750DW series" in vm.selectedScanner
    assert "接続完了" in vm.statusMessage
