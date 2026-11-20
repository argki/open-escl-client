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
    assert (QML_DIR / "CropOverlay.qml").is_file()
    assert (QML_DIR / "CropSettingsPanel.qml").is_file()


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
    assert root.findChild(QObject, "cropOverlay") is not None
    assert root.findChild(QObject, "cropThumbnailsStrip") is not None
    assert root.findChild(QObject, "cropSettingsPanel") is not None
    assert root.findChild(QObject, "autoCropCheckBox") is not None
    assert root.findChild(QObject, "multiDocumentCheckBox") is not None
    assert root.findChild(QObject, "autoOrientationCheckBox") is not None
    assert root.findChild(QObject, "detectionPresetComboBox") is not None
    assert root.findChild(QObject, "resolvedPresetLabel") is not None
    assert root.findChild(QObject, "cropMarginSlider") is not None
    assert root.findChild(QObject, "fineAngleSlider") is not None
    assert root.findChild(QObject, "rotateLeftButton") is not None
    assert root.findChild(QObject, "rotateRightButton") is not None
    assert root.findChild(QObject, "redetectCropButton") is not None
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


def test_preview_crop_overlay_adjustments_and_rotation_ui_binding() -> None:
    """Preview with 2 rotated docs populates CropOverlay, thumbnails, and interactive controls."""
    import cv2
    import numpy as np
    from PySide6.QtCore import QSize

    from open_escl_client.imaging.multi_crop import encode_image_bytes
    from open_escl_client.imaging.orientation import (
        AutoOrientationCorrector,
        HeuristicOrientationBackend,
        OrientationConfig,
    )

    _ensure_qapp()

    # Build a synthetic platen preview containing 2 clearly separated documents.
    canvas = np.full((800, 1000, 3), (30, 30, 32), dtype=np.uint8)
    doc1 = np.full((340, 260, 3), (245, 245, 242), dtype=np.uint8)
    cv2.putText(doc1, "DOC 1", (30, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (25, 25, 25), 2)
    doc2 = np.full((300, 360, 3), (242, 244, 248), dtype=np.uint8)
    cv2.putText(doc2, "DOC 2", (30, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (25, 25, 25), 2)

    canvas[120:460, 110:370] = doc1
    canvas[260:560, 520:880] = doc2
    preview_jpeg = encode_image_bytes(canvas, fmt="JPEG", quality=92)

    brother_caps = parse_scanner_capabilities_xml(BROTHER_CAPABILITIES_XML)
    mock_fetcher = MagicMock(return_value=brother_caps)
    mock_scan_client = MagicMock()
    mock_scan_client.scan_single_page.side_effect = lambda req: EsclScanResult(
        image_bytes=preview_jpeg,
        content_type="image/jpeg",
        job_url="http://192.168.1.50:80/eSCL/ScanJobs/1",
        request=req,
    )

    vm = AppViewModel(
        capabilities_fetcher=mock_fetcher,
        preview_service=PreviewScanService(scan_client_factory=lambda _url: mock_scan_client),
        orientation_corrector=AutoOrientationCorrector(
            config=OrientationConfig(prefer_native_backend=False),
            backends=[HeuristicOrientationBackend()],
        ),
        run_preview_async=False,
    )
    vm.addManualScanner("192.168.1.50")

    engine, _ = create_qml_engine(vm)
    root = engine.rootObjects()[0]

    overlay = root.findChild(QObject, "cropOverlay")
    thumb_strip = root.findChild(QObject, "cropThumbnailsStrip")
    assert overlay is not None
    assert thumb_strip is not None
    assert overlay.property("visible") is False

    vm.requestPreview()

    assert vm.cropCount == 2
    assert overlay.property("visible") is True
    assert thumb_strip.property("visible") is True

    boxes_initial = vm.cropBoxes
    assert len(boxes_initial) == 2
    assert boxes_initial[0]["index"] == 1
    assert boxes_initial[1]["index"] == 2
    assert str(boxes_initial[0]["thumbnailUrl"]).startswith("image://preview/crop/1/")

    # Verify PreviewImageProvider actually serves the generated crop thumbnail QImage.
    thumb_id = str(boxes_initial[0]["thumbnailUrl"]).removeprefix("image://preview/")
    qimg = vm.image_provider.requestImage(thumb_id, QSize(), QSize())
    assert not qimg.isNull()
    assert qimg.width() > 20 and qimg.height() > 20

    # Uniform negative margin shrinks cropped width/height.
    orig_w = int(boxes_initial[0]["widthPx"])
    vm.cropMarginMm = -2.0
    assert int(vm.cropBoxes[0]["widthPx"]) < orig_w

    # Select second crop box and apply manual fine angle + 90° CW/CCW rotations.
    vm.selectCropBox(1)
    assert vm.selectedCropIndex == 1
    vm.setSelectedCropFineAngle(2.5)
    assert vm.selectedCropFineAngle == 2.5

    rot_before = vm.selectedCropRotationDeg
    vm.rotateSelectedCropRight()
    assert vm.selectedCropRotationDeg == (rot_before + 90) % 360
    vm.rotateSelectedCropLeft()
    assert vm.selectedCropRotationDeg == rot_before

    # Move & corner drag update normalized geometry.
    cx_before = float(vm.cropBoxes[1]["centerXNorm"])
    vm.moveCropBoxByNorm(1, 0.02, -0.01)
    assert float(vm.cropBoxes[1]["centerXNorm"]) > cx_before

    vm.dragCropCornerToNorm(1, 0, 0.48, 0.22)
    assert len(vm.cropBoxes[1]["cornersNorm"]) == 4

    # Reset restores manual fine angle to 0.0.
    vm.resetSelectedCropAdjustments()
    assert vm.selectedCropFineAngle == 0.0

    # Toggling autoCropEnabled hides overlay and toggling back re-detects.
    vm.autoCropEnabled = False
    assert vm.cropCount == 0
    assert overlay.property("visible") is False

    vm.autoCropEnabled = True
    assert vm.cropCount == 2
    assert overlay.property("visible") is True

    # Single-document mode detects only 1 box.
    vm.multiDocumentEnabled = False
    assert vm.cropCount == 1

    for obj in engine.rootObjects():
        obj.deleteLater()
    engine.deleteLater()


def test_viewmodel_preview_adjust_then_multi_crop_scan_and_save(tmp_path: Path) -> None:
    """ViewModel connects preview-adjusted multi-crop boxes into main scan & save pipeline."""
    import cv2
    import numpy as np

    from open_escl_client.imaging.multi_crop import encode_image_bytes
    from open_escl_client.imaging.orientation import (
        AutoOrientationCorrector,
        HeuristicOrientationBackend,
        OrientationConfig,
    )

    _ensure_qapp()
    save_dir = tmp_path / "MultiOut"

    canvas = np.full((800, 1000, 3), (28, 28, 30), dtype=np.uint8)
    cv2.rectangle(canvas, (100, 140), (380, 620), (245, 245, 242), thickness=-1)
    cv2.rectangle(canvas, (540, 180), (900, 620), (242, 244, 248), thickness=-1)
    scan_jpeg = encode_image_bytes(canvas, fmt="JPEG", quality=92)

    brother_caps = parse_scanner_capabilities_xml(BROTHER_CAPABILITIES_XML)
    mock_fetcher = MagicMock(return_value=brother_caps)
    mock_scan_client = MagicMock()
    mock_scan_client.scan_single_page.side_effect = lambda req: EsclScanResult(
        image_bytes=scan_jpeg,
        content_type="image/jpeg",
        job_url="http://192.168.1.50:80/eSCL/ScanJobs/1",
        request=req,
    )

    vm = AppViewModel(
        capabilities_fetcher=mock_fetcher,
        scan_client_factory=lambda _url: mock_scan_client,
        orientation_corrector=AutoOrientationCorrector(
            config=OrientationConfig(prefer_native_backend=False),
            backends=[HeuristicOrientationBackend()],
        ),
        run_preview_async=False,
    )
    vm.addManualScanner("192.168.1.50")
    vm.setSaveDirectoryFromUrl(save_dir.as_uri())
    vm.namingRule = "Multi_{seq:03d}"
    vm.selectedSaveFormat = "PNG"

    # 1) Fast preview detects 2 documents; rotate doc #1 manually by 90° CW.
    vm.requestPreview()
    assert vm.cropCount == 2
    vm.selectCropBox(0)
    vm.rotateSelectedCropRight()

    # 2) Main scan & save exports 2 sequential files with box #1 rotated to landscape.
    vm.requestScanAndSave()
    assert vm.savedFileCount == 2
    assert (save_dir / "Multi_001.png").is_file()
    assert (save_dir / "Multi_002.png").is_file()
    assert "2件を一括保存" in vm.statusMessage

    with Image.open(save_dir / "Multi_001.png") as img1:
        assert img1.width > img1.height

    # 3) Second continuous scan auto-detects again and continues sequence 003, 004.
    vm.requestScanAndSave()
    assert vm.savedFileCount == 4
    assert (save_dir / "Multi_003.png").is_file()
    assert (save_dir / "Multi_004.png").is_file()


def test_viewmodel_auto_90_rotation_reflects_in_saved_jpeg_and_syncs_preview_state(
    tmp_path: Path,
) -> None:
    """Preview-detected 90° CW auto-rotation is applied to saved JPEG and synced back to preview."""
    import cv2
    import numpy as np

    from open_escl_client.imaging.multi_crop import encode_image_bytes
    from open_escl_client.imaging.orientation import (
        AutoOrientationCorrector,
        HeuristicOrientationBackend,
        OrientationConfig,
        rotate_image_cw,
    )

    _ensure_qapp()
    save_dir = tmp_path / "RotatedOut"

    # Create upright portrait A4-like document, then rotate 270° CW (90° CCW) so it needs 90° CW.
    upright = np.full((760, 540, 3), (247, 246, 242), dtype=np.uint8)
    lines = [
        ("OFFICIAL NOTICE", 0.95),
        ("Date: 2026-11-18", 0.65),
        ("Section 1: Registration", 0.62),
        ("Section 2: Insurance", 0.62),
        ("Section 3: Pension", 0.62),
        ("Total:", 0.65),
        ("Done.", 0.62),
    ]
    y = 90
    for text, scale in lines:
        cv2.putText(
            upright, text, (45, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (25, 25, 25), 2, cv2.LINE_AA
        )
        y += 75
    sideways = rotate_image_cw(upright, 270)
    assert sideways.shape[1] > sideways.shape[0]
    sideways_jpeg = encode_image_bytes(sideways, fmt="JPEG", quality=92)

    brother_caps = parse_scanner_capabilities_xml(BROTHER_CAPABILITIES_XML)
    mock_fetcher = MagicMock(return_value=brother_caps)
    mock_scan_client = MagicMock()
    mock_scan_client.scan_single_page.side_effect = lambda req: EsclScanResult(
        image_bytes=sideways_jpeg,
        content_type="image/jpeg",
        job_url="http://192.168.1.50:80/eSCL/ScanJobs/1",
        request=req,
    )

    vm = AppViewModel(
        capabilities_fetcher=mock_fetcher,
        scan_client_factory=lambda _url: mock_scan_client,
        orientation_corrector=AutoOrientationCorrector(
            config=OrientationConfig(prefer_native_backend=False, min_confidence=0.2),
            backends=[HeuristicOrientationBackend()],
        ),
        run_preview_async=False,
    )
    vm.addManualScanner("192.168.1.50")
    vm.setSaveDirectoryFromUrl(save_dir.as_uri())
    vm.namingRule = "AutoRot_{seq:03d}"
    vm.selectedSaveFormat = "JPEG"

    # Preview detects full-page A4 with 90° CW auto-rotation.
    vm.requestPreview()
    assert vm.cropCount == 1
    assert vm.cropBoxes[0]["effectiveRotationDeg"] == 90

    # Save must produce an upright portrait JPEG and update preview dimensions & status message.
    vm.requestScanAndSave()
    out1 = save_dir / "AutoRot_001.jpg"
    assert out1.is_file()
    assert "回転 90°" in vm.statusMessage
    with Image.open(out1) as img1:
        assert img1.height > img1.width
    assert vm.previewImageHeight > vm.previewImageWidth

    # Continuous 2nd scan without re-previewing must also save an upright portrait JPEG.
    vm.requestScanAndSave()
    out2 = save_dir / "AutoRot_002.jpg"
    assert out2.is_file()
    with Image.open(out2) as img2:
        assert img2.height > img2.width


def test_viewmodel_detection_preset_selection_and_a4_on_a3_auto_resolution() -> None:
    """ViewModel auto-selects `standard_sheet` for A4-on-A3 and allows manual preset switching."""
    import cv2
    import numpy as np

    from open_escl_client.imaging.multi_crop import encode_image_bytes

    _ensure_qapp()

    # Build A4 sheet (511x726) placed on an A3 white platen (717x1024).
    w, h = 717, 1024
    canvas = np.full((h, w, 3), (254, 254, 254), dtype=np.uint8)
    cv2.rectangle(canvas, (81, 83), (592, 809), (251, 251, 251), thickness=-1)
    cv2.line(canvas, (81, 83), (592, 83), (238, 238, 238), thickness=2)
    cv2.rectangle(canvas, (130, 121), (540, 138), (30, 30, 30), thickness=-1)
    cv2.rectangle(canvas, (98, 150), (576, 752), (232, 232, 232), thickness=-1)
    cv2.rectangle(canvas, (98, 150), (576, 752), (35, 35, 35), thickness=2)
    for row_y in range(195, 740, 42):
        cv2.line(canvas, (98, row_y), (576, row_y), (50, 50, 50), thickness=1)

    scan_png = encode_image_bytes(canvas, fmt="PNG")
    brother_caps = parse_scanner_capabilities_xml(BROTHER_CAPABILITIES_XML)
    mock_fetcher = MagicMock(return_value=brother_caps)
    mock_scan_client = MagicMock()
    mock_scan_client.scan_single_page.side_effect = lambda req: EsclScanResult(
        image_bytes=scan_png,
        content_type="image/png",
        job_url="http://192.168.1.50:80/eSCL/ScanJobs/1",
        request=req,
    )

    vm = AppViewModel(
        capabilities_fetcher=mock_fetcher,
        scan_client_factory=lambda _url: mock_scan_client,
        run_preview_async=False,
    )
    vm.addManualScanner("192.168.1.50")

    assert len(vm.detectionPresets) == 4
    assert vm.detectionPresetCode == "auto"
    assert vm.selectedDetectionPreset == "自動判別 (推奨)"

    vm.requestPreview()
    assert vm.cropCount == 1
    assert vm.resolvedDetectionPreset == "standard_sheet"
    assert "自動採用: 単票・定型紙" in vm.resolvedDetectionPresetLabel
    box_auto = vm.cropBoxes[0]
    assert 500 <= box_auto["widthPx"] <= 522
    assert 716 <= box_auto["heightPx"] <= 736

    # Verify QML sidebar layout does not overflow horizontally when long resolved label is active.
    engine, _ = create_qml_engine(vm)
    root = engine.rootObjects()[0]
    crop_panel = root.findChild(QObject, "cropSettingsPanel")
    save_panel = root.findChild(QObject, "saveSettingsPanel")
    sens_slider = root.findChild(QObject, "detectionSensitivitySlider")
    ratio_combo = root.findChild(QObject, "aspectRatioPresetComboBox")
    assert (
        crop_panel is not None
        and save_panel is not None
        and sens_slider is not None
        and ratio_combo is not None
    )
    assert float(crop_panel.property("width")) <= float(save_panel.property("width")) + 1.0
    for obj in engine.rootObjects():
        obj.deleteLater()
    engine.deleteLater()

    # Adjusting detectionSensitivity triggers immediate re-detection with clamped [-5, +5] range.
    vm.detectionSensitivity = 9
    assert vm.detectionSensitivity == 5
    assert vm.cropCount == 1
    vm.detectionSensitivity = 0
    assert vm.detectionSensitivity == 0

    # User switches to "スキャン範囲全面" in UI -> immediately recalculates to full A3 bounds.
    vm.selectedDetectionPreset = "スキャン範囲全面"
    assert vm.detectionPresetCode == "full_page"
    assert vm.resolvedDetectionPreset == "full_page"
    assert "手動指定: スキャン範囲全面" in vm.resolvedDetectionPresetLabel
    box_full = vm.cropBoxes[0]
    assert box_full["widthPx"] == w
    assert box_full["heightPx"] == h

    # User switches back to "単票・定型紙 (A3内のA4等)" -> crops to A4 sheet again.
    vm.selectedDetectionPreset = "単票・定型紙 (A3内のA4等)"
    assert vm.detectionPresetCode == "standard_sheet"
    assert vm.resolvedDetectionPreset == "standard_sheet"
    assert "手動指定: 単票・定型紙" in vm.resolvedDetectionPresetLabel
    box_std = vm.cropBoxes[0]
    assert 500 <= box_std["widthPx"] <= 522
    assert 716 <= box_std["heightPx"] <= 736


def test_viewmodel_per_crop_sensitivity_redetects_only_selected_box(tmp_path: Path) -> None:
    """Changing `selectedCropSensitivity` re-detects only the selected crop box."""
    # Left document has subtle white margin (251) on white platen (254); right document is distinct.
    w, h = 1152, 800
    img = Image.new("RGB", (w, h), (254, 254, 254))
    arr = __import__("numpy").array(img)
    cv2 = __import__("cv2")
    # Left subtle sheet: x=60..420, y=100..680 with inner printed block x=140..340, y=200..560
    cv2.rectangle(arr, (60, 100), (420, 680), (251, 251, 251), -1)
    cv2.rectangle(arr, (140, 200), (340, 560), (45, 45, 45), -1)
    # Right distinct sheet: x=620..1020, y=140..640
    cv2.rectangle(arr, (620, 140), (1020, 640), (225, 232, 240), -1)
    cv2.rectangle(arr, (680, 200), (960, 580), (50, 50, 50), -1)

    buf = BytesIO()
    Image.fromarray(arr).save(buf, format="JPEG", quality=98)
    preview_bytes = buf.getvalue()

    caps = parse_scanner_capabilities_xml(BROTHER_CAPABILITIES_XML)
    mock_fetcher = MagicMock(return_value=caps)
    mock_client = MagicMock()
    mock_client.scan_single_page.side_effect = lambda req: EsclScanResult(
        image_bytes=preview_bytes,
        content_type="image/jpeg",
        job_url="http://192.168.1.50:80/eSCL/ScanJobs/1",
        request=req,
    )

    vm = AppViewModel(
        config_store=UserConfigStore(tmp_path / "config.json"),
        capabilities_fetcher=mock_fetcher,
        scan_client_factory=lambda _url: mock_client,
        run_preview_async=False,
    )
    vm.addManualScanner("192.168.1.50")
    vm.requestPreview()

    assert vm.cropCount == 2
    boxes_before = list(vm.cropBoxes)
    left_before = boxes_before[0]
    right_before = boxes_before[1]

    # Select only box #1 (index 0) and raise its sensitivity to +5.
    vm.selectCropBox(0)
    assert vm.selectedCropSensitivity == 0
    vm.selectedCropSensitivity = 5
    assert vm.selectedCropSensitivity == 5

    boxes_after = list(vm.cropBoxes)
    left_after = boxes_after[0]
    right_after = boxes_after[1]

    # Left box expanded to capture its subtle 251-luminance paper margin; right box stayed identical.
    assert left_after["widthPx"] > left_before["widthPx"] + 40
    assert abs(right_after["widthPx"] - right_before["widthPx"]) < 1e-3
    assert abs(right_after["heightPx"] - right_before["heightPx"]) < 1e-3
    assert abs(right_after["centerXNorm"] - right_before["centerXNorm"]) < 1e-4

    # Switching selection to box #2 shows its own untouched sensitivity (0).
    vm.selectCropBox(1)
    assert vm.selectedCropSensitivity == 0
    assert vm.selectedCropAspectRatioPreset == "自動判別 (推奨)"

    # Changing aspect ratio preset on box #2 leaves box #1 untouched.
    assert "A判・B判 / 白銀比 (1:1.414)" in vm.aspectRatioPresets
    assert "はがき (100×148mm / 1:1.480)" in vm.aspectRatioPresets
    assert "L判写真 (89×127mm / 1:1.427)" in vm.aspectRatioPresets
    vm.selectedCropAspectRatioPreset = "A判・B判 / 白銀比 (1:1.414)"
    assert vm.selectedCropAspectRatioPreset == "A判・B判 / 白銀比 (1:1.414)"
    vm.selectCropBox(0)
    assert vm.selectedCropAspectRatioPreset == "自動判別 (推奨)"



def test_viewmodel_bleed_through_preview_updates_immediately_without_rescan(
    tmp_path: Path,
) -> None:
    """Toggling bleed-through settings updates preview & thumbnails without rescanning."""
    import numpy as np

    w, h = 600, 800
    arr = np.full((h, w, 3), (244, 246, 248), dtype=np.uint8)
    # Faint reverse bleed-through bar and crisp foreground bar
    import cv2

    cv2.rectangle(arr, (80, 120), (520, 155), (200, 202, 204), -1)
    cv2.rectangle(arr, (80, 340), (520, 375), (30, 30, 30), -1)

    buf = BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    preview_bytes = buf.getvalue()

    caps = parse_scanner_capabilities_xml(BROTHER_CAPABILITIES_XML)
    mock_fetcher = MagicMock(return_value=caps)
    mock_client = MagicMock()
    mock_client.scan_single_page.side_effect = lambda req: EsclScanResult(
        image_bytes=preview_bytes,
        content_type="image/png",
        job_url="http://192.168.1.50:80/eSCL/ScanJobs/1",
        request=req,
    )

    vm = AppViewModel(
        config_store=UserConfigStore(tmp_path / "config.json"),
        capabilities_fetcher=mock_fetcher,
        scan_client_factory=lambda _url: mock_client,
        run_preview_async=False,
    )
    vm.addManualScanner("192.168.1.50")
    vm.requestPreview()
    assert mock_client.scan_single_page.call_count == 1
    assert vm.bleedThroughEnabled is True
    assert vm.bleedThroughWhitenBackground is True
    initial_url = vm.previewImageUrl

    # Toggling bleed-through removal off and back on updates preview URL without new scan.
    vm.bleedThroughEnabled = False
    assert vm.bleedThroughEnabled is False
    url_after_disable = vm.previewImageUrl
    assert url_after_disable != initial_url

    vm.bleedThroughEnabled = True
    assert vm.bleedThroughEnabled is True
    url_after_enable = vm.previewImageUrl
    assert url_after_enable != url_after_disable
    assert mock_client.scan_single_page.call_count == 1

    # Adjust strength & whiten background -> updates again without rescanning.
    vm.bleedThroughStrength = 0.9
    assert vm.bleedThroughStrength == 0.9
    url_after_strength = vm.previewImageUrl
    assert url_after_strength != url_after_enable

    vm.bleedThroughWhitenBackground = False
    assert vm.bleedThroughWhitenBackground is False
    url_after_unwhiten = vm.previewImageUrl
    assert url_after_unwhiten != url_after_strength

    vm.bleedThroughWhitenBackground = True
    assert vm.bleedThroughWhitenBackground is True
    assert vm.previewImageUrl != url_after_unwhiten
    assert mock_client.scan_single_page.call_count == 1

    # Verify BleedThroughPanel QML components load and stay within sidebar width bounds.
    engine, _ = create_qml_engine(vm)
    root = engine.rootObjects()[0]
    bleed_panel = root.findChild(QObject, "bleedThroughPanel")
    save_panel = root.findChild(QObject, "saveSettingsPanel")
    bleed_cb = root.findChild(QObject, "bleedThroughCheckBox")
    bleed_slider = root.findChild(QObject, "bleedThroughStrengthSlider")
    bleed_whiten_cb = root.findChild(QObject, "bleedThroughWhitenCheckBox")
    assert bleed_panel is not None and save_panel is not None
    assert bleed_cb is not None and bleed_slider is not None and bleed_whiten_cb is not None
    assert bleed_cb.property("checked") is True
    assert int(bleed_slider.property("value")) == 90
    assert bleed_whiten_cb.property("checked") is True
    assert float(bleed_panel.property("width")) <= float(save_panel.property("width")) + 1.0
    for obj in engine.rootObjects():
        obj.deleteLater()
    engine.deleteLater()


def test_viewmodel_persists_all_settings_and_resets_to_defaults(tmp_path: Path) -> None:
    """All scan/preview/crop/enhancement preferences persist across restart and reset cleanly."""
    import numpy as np

    buf = BytesIO()
    Image.fromarray(np.full((400, 300, 3), 250, dtype=np.uint8)).save(buf, format="JPEG")
    preview_bytes = buf.getvalue()

    caps = parse_scanner_capabilities_xml(BROTHER_CAPABILITIES_XML)
    mock_fetcher = MagicMock(return_value=caps)
    recorded_preview_dpis: list[int] = []

    def _scan(req: EsclScanRequest) -> EsclScanResult:
        recorded_preview_dpis.append(req.resolution_dpi)
        return EsclScanResult(
            image_bytes=preview_bytes,
            content_type="image/jpeg",
            job_url="http://192.168.1.50:80/eSCL/ScanJobs/1",
            request=req,
        )

    mock_client = MagicMock()
    mock_client.scan_single_page.side_effect = _scan
    cfg_store = UserConfigStore(tmp_path / "config.json")

    vm = AppViewModel(
        config_store=cfg_store,
        capabilities_fetcher=mock_fetcher,
        scan_client_factory=lambda _url: mock_client,
        run_preview_async=False,
    )
    vm.addManualScanner("192.168.1.50")

    vm.selectedInputSource = "ADF (片面)"
    target_paper_label = next(lbl for lbl in vm.paperSizes if "B5" in lbl or "横置き" in lbl)
    vm.selectedPaperSize = target_paper_label
    vm.selectedResolution = 600
    vm.selectedPreviewDpi = 300
    vm.selectedSaveFormat = "JPEG"
    vm.namingRule = "Custom_{date}_{seq:02d}"
    vm.autoCropEnabled = False
    vm.multiDocumentEnabled = False
    vm.autoOrientationEnabled = False
    vm.selectedDetectionPreset = "単票・定型紙 (A3内のA4等)"
    vm.detectionSensitivity = 3
    vm.selectedCropAspectRatioPreset = "はがき (100×148mm / 1:1.480)"
    vm.cropMarginMm = -2.0
    vm.bleedThroughEnabled = False
    vm.bleedThroughStrength = 0.8
    vm.bleedThroughWhitenBackground = False

    # Reload into a brand new AppViewModel and connect scanner -> restores A3/ADF/150dpi/crop!
    vm2 = AppViewModel(
        config_store=cfg_store,
        capabilities_fetcher=mock_fetcher,
        scan_client_factory=lambda _url: mock_client,
        run_preview_async=False,
    )
    assert vm2.selectedPreviewDpi == 300
    assert vm2.selectedSaveFormat == "JPEG"
    assert vm2.namingRule == "Custom_{date}_{seq:02d}"
    assert vm2.autoCropEnabled is False
    assert vm2.multiDocumentEnabled is False
    assert vm2.autoOrientationEnabled is False
    assert vm2.detectionPresetCode == "standard_sheet"
    assert vm2.detectionSensitivity == 3
    assert vm2.selectedCropAspectRatioPreset == "はがき (100×148mm / 1:1.480)"
    assert vm2.cropMarginMm == -2.0
    assert vm2.bleedThroughEnabled is False
    assert vm2.bleedThroughStrength == 0.8
    assert vm2.bleedThroughWhitenBackground is False

    vm2.addManualScanner("192.168.1.50")
    assert vm2.selectedPaperSize == target_paper_label
    assert vm2.selectedInputSource == "ADF (片面)"
    assert vm2.selectedResolution == 600
    assert vm2.selectedPreviewDpi == 300
    assert 300 in vm2.previewDpiChoices

    vm2.requestPreview()
    assert recorded_preview_dpis[-1] == 300
    assert vm2.previewDpi == 300

    # Reset all settings back to factory defaults
    vm2.resetAllSettingsToDefaults()
    assert vm2.namingRule == "Scan_{date}_{time}_{seq:03d}"
    assert vm2.selectedSaveFormat == "PDF"
    assert vm2.selectedResolution == 300
    assert vm2.selectedPreviewDpi == 100
    assert vm2.autoCropEnabled is True
    assert vm2.multiDocumentEnabled is True
    assert vm2.autoOrientationEnabled is True
    assert vm2.detectionPresetCode == "auto"
    assert vm2.detectionSensitivity == 0
    assert vm2.selectedCropAspectRatioPreset == "自動判別 (推奨)"
    assert vm2.cropMarginMm == 0.0
    assert vm2.bleedThroughEnabled is True
    assert vm2.bleedThroughStrength == 0.6
    assert vm2.bleedThroughWhitenBackground is True
    assert "A4" in vm2.selectedPaperSize

    # Verify QML previewDpiCombo, resetAllSettingsButton, and resetSettingsConfirmDialog load.
    engine, _ = create_qml_engine(vm2)
    root = engine.rootObjects()[0]
    preview_dpi_combo = root.findChild(QObject, "previewDpiCombo")
    reset_btn = root.findChild(QObject, "resetAllSettingsButton")
    reset_dialog = root.findChild(QObject, "resetSettingsConfirmDialog")
    assert preview_dpi_combo is not None and reset_btn is not None and reset_dialog is not None
    assert preview_dpi_combo.property("currentText") == "100"
    vm2.namingRule = "TempChanged_{seq:02d}"
    reset_dialog.accept()
    assert vm2.namingRule == "Scan_{date}_{time}_{seq:03d}"
    for obj in engine.rootObjects():
        obj.deleteLater()
    engine.deleteLater()


def test_preview_area_fits_within_window_and_provides_wheel_zoom_and_scrollbars() -> None:
    """Preview header/footer never overflow right edge at initial/min width; wheel/scroll exist."""
    import numpy as np
    from PySide6.QtCore import QCoreApplication

    _ensure_qapp()
    arr = np.full((600, 800, 3), 30, dtype=np.uint8)
    arr[100:500, 150:450] = 245
    buf = BytesIO()
    Image.fromarray(arr).save(buf, format="JPEG")
    preview_bytes = buf.getvalue()

    caps = parse_scanner_capabilities_xml(BROTHER_CAPABILITIES_XML)
    mock_fetcher = MagicMock(return_value=caps)
    mock_client = MagicMock()
    mock_client.scan_single_page.side_effect = lambda req: EsclScanResult(
        image_bytes=preview_bytes,
        content_type="image/jpeg",
        job_url="http://192.168.1.50:80/eSCL/ScanJobs/1",
        request=req,
    )

    vm = AppViewModel(
        capabilities_fetcher=mock_fetcher,
        scan_client_factory=lambda _url: mock_client,
        run_preview_async=False,
    )
    vm.addManualScanner("192.168.1.50")
    vm.requestPreview()
    assert vm.cropCount == 1

    engine, _ = create_qml_engine(vm)
    root = engine.rootObjects()[0]
    preview_area = root.findChild(QObject, "previewArea")
    zoom_row = root.findChild(QObject, "zoomControlsRow")
    hint_label = root.findChild(QObject, "cropStripHintLabel")
    wheel_handler = root.findChild(QObject, "previewWheelHandler")
    v_scroll = root.findChild(QObject, "previewVerticalScrollBar")
    h_scroll = root.findChild(QObject, "previewHorizontalScrollBar")

    assert preview_area is not None and zoom_row is not None and hint_label is not None
    assert wheel_handler is not None and v_scroll is not None and h_scroll is not None

    for win_w in (1180, 920):
        root.setProperty("width", win_w)
        QCoreApplication.processEvents()
        pa_w = float(preview_area.property("width"))
        zoom_right = float(zoom_row.property("x")) + float(zoom_row.property("width")) + 20.0
        hint_right = float(hint_label.property("x")) + float(hint_label.property("width")) + 28.0
        assert pa_w > 0.0
        assert zoom_right <= pa_w + 1.0
        assert hint_right <= pa_w + 1.0

    for obj in engine.rootObjects():
        obj.deleteLater()
    engine.deleteLater()


def test_crop_card_click_inspects_document_and_full_card_returns_to_overview() -> None:
    """Clicking a crop card switches upper canvas to cropped result; fullOverviewCard restores."""
    import numpy as np
    from PySide6.QtCore import QCoreApplication

    _ensure_qapp()
    arr = np.full((600, 800, 3), 30, dtype=np.uint8)
    arr[100:500, 150:450] = 245
    buf = BytesIO()
    Image.fromarray(arr).save(buf, format="JPEG")
    preview_bytes = buf.getvalue()

    caps = parse_scanner_capabilities_xml(BROTHER_CAPABILITIES_XML)
    mock_fetcher = MagicMock(return_value=caps)
    mock_client = MagicMock()
    mock_client.scan_single_page.side_effect = lambda req: EsclScanResult(
        image_bytes=preview_bytes,
        content_type="image/jpeg",
        job_url="http://192.168.1.50:80/eSCL/ScanJobs/1",
        request=req,
    )

    vm = AppViewModel(
        capabilities_fetcher=mock_fetcher,
        scan_client_factory=lambda _url: mock_client,
        run_preview_async=False,
    )
    vm.addManualScanner("192.168.1.50")
    vm.requestPreview()
    assert vm.cropCount == 1
    assert vm.isInspectingCrop is False
    assert vm.activePreviewMode == "full"
    assert vm.fullPreviewThumbnailUrl.startswith("image://preview/full_thumb/")
    full_url = vm.previewImageUrl
    assert vm.previewImageWidth == 800
    assert vm.previewImageHeight == 600

    engine, _ = create_qml_engine(vm)
    root = engine.rootObjects()[0]
    full_card = root.findChild(QObject, "fullOverviewCard")
    crop_overlay = root.findChild(QObject, "cropOverlay")
    title_label = root.findChild(QObject, "previewTitleLabel")
    assert full_card is not None
    assert crop_overlay is not None
    assert title_label is not None
    assert bool(crop_overlay.property("visible")) is True

    # Inspect cropped document
    vm.inspectCropPreview(0)
    QCoreApplication.processEvents()
    assert vm.isInspectingCrop is True
    assert vm.activePreviewMode == "crop"
    assert vm.previewImageUrl.startswith("image://preview/inspect/")
    assert 0 < vm.previewImageWidth < 800
    assert 0 < vm.previewImageHeight < 600
    assert bool(crop_overlay.property("visible")) is False
    assert "切り出し結果 #1" in str(title_label.property("text"))

    # Live rotation while inspecting refreshes inspect URL
    prev_inspect_url = vm.previewImageUrl
    vm.rotateCropBox(0, 90)
    QCoreApplication.processEvents()
    assert vm.isInspectingCrop is True
    assert vm.previewImageUrl != prev_inspect_url
    assert vm.previewImageUrl.startswith("image://preview/inspect/")

    # Return to full overview
    vm.showFullPreview()
    QCoreApplication.processEvents()
    assert vm.isInspectingCrop is False
    assert vm.activePreviewMode == "full"
    assert vm.previewImageUrl == full_url
    assert vm.previewImageWidth == 800
    assert vm.previewImageHeight == 600
    assert bool(crop_overlay.property("visible")) is True

    for obj in engine.rootObjects():
        obj.deleteLater()
    engine.deleteLater()






