"""Tests for declarative QML loading, AppViewModel bindings, preview canvas, and continuous save workflow."""

from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
from unittest.mock import MagicMock
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication

from open_escl_client.config.user_config import UserConfigStore
from open_escl_client.core.preview_service import PreviewScanService
from open_escl_client.escl.capabilities import parse_scanner_capabilities_xml
from open_escl_client.escl.discovery import DiscoveredScanner
from open_escl_client.escl.scan_client import EsclScanRequest, EsclScanResult
from open_escl_client.ui.app import MAIN_QML_PATH, QML_DIR, create_qml_engine
from open_escl_client.ui.viewmodel import AppViewModel
from tests.escl.test_capabilities import BROTHER_CAPABILITIES_XML, GENERIC_A3_PROFILE_REF_XML


def _ensure_qapp() -> QGuiApplication:
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app  # type: ignore[return-value]


def _make_jpeg_bytes(width: int = 400, height: int = 300) -> bytes:
    buf = BytesIO()
    img = Image.new("RGB", (width, height), color=(200, 220, 240))
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_main_qml_and_component_files_exist() -> None:
    assert MAIN_QML_PATH.is_file()
    assert (QML_DIR / "PaperPlacementPreview.qml").is_file()
    assert (QML_DIR / "SaveSettingsPanel.qml").is_file()


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
    assert root.findChild(QObject, "saveSettingsPanel") is not None
    assert root.findChild(QObject, "saveDirField") is not None
    assert root.findChild(QObject, "chooseFolderButton") is not None
    assert root.findChild(QObject, "namingRuleField") is not None
    assert root.findChild(QObject, "nextFilenamePreviewLabel") is not None
    assert root.findChild(QObject, "saveFormatCombo") is not None
    assert root.findChild(QObject, "jpegQualitySlider") is not None
    assert root.findChild(QObject, "multipagePdfCheckBox") is not None
    assert root.findChild(QObject, "previewArea") is not None
    assert root.findChild(QObject, "previewCanvasImage") is not None
    assert root.findChild(QObject, "busyOverlay") is not None
    assert root.findChild(QObject, "actionToolbar") is not None
    assert root.findChild(QObject, "savedCountLabel") is not None
    assert root.findChild(QObject, "finalizePdfButton") is not None
    assert root.findChild(QObject, "previewButton") is not None
    assert root.findChild(QObject, "cancelScanButton") is not None
    assert root.findChild(QObject, "scanAndSaveButton") is not None
    assert root.findChild(QObject, "placementGuidePopup") is not None
    assert root.findChild(QObject, "paperPlacementPreview") is not None
    assert root.findChild(QObject, "scannerBedRect") is not None
    assert root.findChild(QObject, "paperSheetRect") is not None

    for obj in roots:
        obj.deleteLater()
    engine.deleteLater()
    assert vm is not None


def test_hover_placement_guide_updates_preview_card_and_horizontal_platen_geometry() -> None:
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

    bed_w = float(bed_rect.property("width"))
    bed_h = float(bed_rect.property("height"))
    assert bed_w > 180.0
    assert bed_h > 120.0
    assert bed_w > bed_h

    vm.selectedPaperSize = "A4 縦置き (横 210 × 奥行き 297 mm)"
    assert preview_card.property("orientation") == "portrait"
    paper_w_portrait = float(paper_rect.property("width"))
    paper_h_portrait = float(paper_rect.property("height"))
    assert 50.0 < paper_w_portrait < bed_w
    assert paper_h_portrait > paper_w_portrait

    vm.selectedPaperSize = "A4 横置き (横 297 × 奥行き 210 mm)"
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


def test_fast_preview_scan_updates_image_provider_and_qml_canvas() -> None:
    _ensure_qapp()
    brother_caps = parse_scanner_capabilities_xml(BROTHER_CAPABILITIES_XML)
    mock_fetcher = MagicMock(return_value=brother_caps)

    sample_jpeg = _make_jpeg_bytes(595, 420)
    mock_scan_client = MagicMock()

    def _fake_scan(req: EsclScanRequest) -> EsclScanResult:
        return EsclScanResult(
            image_bytes=sample_jpeg,
            content_type="image/jpeg",
            job_url="http://192.168.1.50:80/eSCL/ScanJobs/1",
            request=req,
        )

    mock_scan_client.scan_single_page.side_effect = _fake_scan
    preview_service = PreviewScanService(scan_client_factory=lambda _url: mock_scan_client)

    vm = AppViewModel(
        capabilities_fetcher=mock_fetcher,
        preview_service=preview_service,
        run_preview_async=False,
    )
    vm.addManualScanner("192.168.1.50")

    engine, _ = create_qml_engine(vm)
    root = engine.rootObjects()[0]

    vm.selectedResolution = 600
    assert vm.hasPreview is False

    vm.requestPreview()

    assert vm.hasPreview is True
    assert vm.previewDpi == 100
    assert vm.previewImageUrl == "image://preview/1"
    assert "100 dpi" in vm.statusMessage
    assert "595×420" in vm.statusMessage

    vm.zoomInPreview()
    assert vm.previewZoom == 1.25
    vm.zoomOutPreview()
    assert vm.previewZoom == 1.0
    vm.zoomInPreview()
    vm.resetPreviewZoom()
    assert vm.previewZoom == 1.0

    preview_img = root.findChild(QObject, "previewCanvasImage")
    assert preview_img is not None
    assert preview_img.property("source").toString() == "image://preview/1"

    for obj in engine.rootObjects():
        obj.deleteLater()
    engine.deleteLater()


def test_continuous_scan_and_save_workflow_with_config_persistence(tmp_path: Path) -> None:
    _ensure_qapp()
    save_dir = tmp_path / "ScansOut"
    config_store = UserConfigStore(config_path=tmp_path / "config.json")

    brother_caps = parse_scanner_capabilities_xml(BROTHER_CAPABILITIES_XML)
    mock_fetcher = MagicMock(return_value=brother_caps)
    sample_jpeg = _make_jpeg_bytes(400, 600)
    mock_scan_client = MagicMock()
    mock_scan_client.scan_single_page.side_effect = lambda req: EsclScanResult(
        image_bytes=sample_jpeg,
        content_type="image/jpeg",
        job_url="http://192.168.1.50:80/eSCL/ScanJobs/1",
        request=req,
    )

    vm = AppViewModel(
        capabilities_fetcher=mock_fetcher,
        scan_client_factory=lambda _url: mock_scan_client,
        config_store=config_store,
        run_preview_async=False,
    )
    vm.addManualScanner("192.168.1.50")

    vm.setSaveDirectoryFromUrl(save_dir.as_uri())
    vm.namingRule = "TestScan_{seq:03d}"
    vm.selectedSaveFormat = "JPEG"
    vm.jpegQuality = 75
    vm.selectedResolution = 300

    assert vm.nextFilenamePreview == "TestScan_001.jpg"

    # Run two continuous scans without re-previewing
    vm.requestScanAndSave()
    assert vm.savedFileCount == 1
    assert vm.lastSavedFileName == "TestScan_001.jpg"
    assert (save_dir / "TestScan_001.jpg").is_file()
    assert vm.nextFilenamePreview == "TestScan_002.jpg"

    vm.requestScanAndSave()
    assert vm.savedFileCount == 2
    assert vm.lastSavedFileName == "TestScan_002.jpg"
    assert (save_dir / "TestScan_002.jpg").is_file()
    assert vm.nextFilenamePreview == "TestScan_003.jpg"

    # Verify preferences persisted and reload properly in a new AppViewModel instance
    vm_reloaded = AppViewModel(config_store=config_store)
    assert vm_reloaded.saveDirectory == str(save_dir)
    assert vm_reloaded.namingRule == "TestScan_{seq:03d}"
    assert vm_reloaded.selectedSaveFormat == "JPEG"
    assert vm_reloaded.jpegQuality == 75

    # Switch to Multi-page PDF mode and scan 2 pages -> finalize
    vm.selectedSaveFormat = "PDF"
    vm.multipagePdf = True
    vm.requestScanAndSave()
    assert vm.pendingPdfPageCount == 1
    vm.requestScanAndSave()
    assert vm.pendingPdfPageCount == 2

    vm.finalizeMultipagePdf()
    assert vm.pendingPdfPageCount == 0
    assert vm.savedFileCount == 3
    assert vm.lastSavedFileName == "TestScan_003.pdf"
    assert (save_dir / "TestScan_003.pdf").is_file()


def test_viewmodel_properties_and_slots() -> None:
    vm = AppViewModel()
    assert "A4 横置き (横 297 × 奥行き 210 mm)" in vm.paperSizes
    assert "B5 (JIS) 横置き (横 257 × 奥行き 182 mm)" in vm.paperSizes
    assert "A5 縦置き (横 148 × 奥行き 210 mm)" in vm.paperSizes
    assert "A5 横置き (横 210 × 奥行き 148 mm)" in vm.paperSizes
    assert "ハガキ 縦置き (横 100 × 奥行き 148 mm)" in vm.paperSizes
    assert "ハガキ 横置き (横 148 × 奥行き 100 mm)" in vm.paperSizes
    assert len(vm.resolutions) >= 6
    assert vm.selectedResolution == 300
    assert vm.selectedSaveFormat == "PDF"
    assert vm.jpegQuality == 85
    assert vm.nextFilenamePreview.endswith(".pdf")

    vm.selectedPaperSize = "B5 (JIS) 横置き (横 257 × 奥行き 182 mm)"
    assert vm.selectedPaperSize == "B5 (JIS) 横置き (横 257 × 奥行き 182 mm)"

    vm.selectedResolution = 600
    assert vm.selectedResolution == 600

    vm.saveDirectory = "/tmp/scans"
    assert vm.saveDirectory == "/tmp/scans"

    vm.namingRule = "Receipt_{date}_{seq:04d}"
    assert vm.namingRule == "Receipt_{date}_{seq:04d}"
    assert vm.nextFilenamePreview.startswith("Receipt_")


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
    assert "A4 横置き (横 297 × 奥行き 210 mm)" in vm.paperSizes
    assert "名刺 縦置き (横 55 × 奥行き 91 mm)" in vm.paperSizes
    assert "名刺 横置き (横 91 × 奥行き 55 mm)" in vm.paperSizes

    vm.selectedInputSource = "ADF (片面)"
    assert vm.resolutions == [100, 300, 600]
    assert "リーガル (Legal) 横置き (横 355.6 × 奥行き 215.9 mm)" in vm.paperSizes
    assert "名刺 縦置き (横 55 × 奥行き 91 mm)" not in vm.paperSizes


def test_viewmodel_a3_scanner_provides_both_a4_orientations_and_placement_guide() -> None:
    a3_caps = parse_scanner_capabilities_xml(GENERIC_A3_PROFILE_REF_XML)
    mock_fetcher = MagicMock(return_value=a3_caps)

    vm = AppViewModel(capabilities_fetcher=mock_fetcher)
    vm.addManualScanner("192.168.1.60")

    assert "A4 縦置き (横 210 × 奥行き 297 mm)" in vm.paperSizes
    assert "A4 横置き (横 297 × 奥行き 210 mm)" in vm.paperSizes

    guide_portrait = vm.getPlacementGuide("A4 縦置き (横 210 × 奥行き 297 mm)")
    assert guide_portrait["orientation"] == "portrait"
    assert guide_portrait["paperWidthMm"] == 210.0
    assert guide_portrait["paperHeightMm"] == 297.0
    assert guide_portrait["bedWidthMm"] > guide_portrait["bedHeightMm"]

    guide_landscape = vm.getPlacementGuide("A4 横置き (横 297 × 奥行き 210 mm)")
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
