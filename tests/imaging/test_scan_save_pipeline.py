"""Integration tests for multi-crop, deskew, auto-orientation, and continuous scan saving."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import cv2
import numpy as np
import pikepdf
from PIL import Image

from open_escl_client.core.continuous_scan import ContinuousScanSession, ScanSaveProfile
from open_escl_client.core.paper_size import PaperOrientation, PaperSizeOption
from open_escl_client.escl.capabilities import InputSourceCaps, InputSourceKind
from open_escl_client.escl.scan_client import EsclScanRequest, EsclScanResult
from open_escl_client.imaging.multi_crop import (
    MultiDocumentDetector,
    encode_image_bytes,
)
from open_escl_client.imaging.orientation import (
    AutoOrientationCorrector,
    HeuristicOrientationBackend,
    OrientationConfig,
    rotate_image_cw,
)
from open_escl_client.imaging.pipeline import (
    MultiCropPipelineConfig,
    MultiCropScanPipeline,
)

FIXED_NOW = datetime(2026, 11, 18, 21, 45, 0)


def _make_platen_and_a3() -> tuple[InputSourceCaps, PaperSizeOption]:
    platen = InputSourceCaps(
        source=InputSourceKind.PLATEN,
        max_width=3507,
        max_height=5100,
        resolutions=(100, 200, 300, 600),
        color_modes=("RGB24", "Grayscale8"),
    )
    a3 = PaperSizeOption(
        code="A3",
        name="A3",
        width_mm=297.0,
        height_mm=420.0,
        width_units=3507,
        height_units=4960,
        orientation=PaperOrientation.PORTRAIT,
    )
    return platen, a3


def _render_portrait_letter_doc(title: str, width: int = 420, height: int = 580) -> np.ndarray:
    """Create a crisp left-aligned upright portrait document (width < height)."""
    doc = np.full((height, width, 3), (246, 245, 240), dtype=np.uint8)
    lines = [
        (title, 0.95, 2),
        ("Date: 2026-11-18", 0.62, 2),
        ("Item A: Scanner client", 0.58, 2),
        ("Item B: Multi-crop deskew", 0.58, 2),
        ("Item C: Auto 90/180/270", 0.58, 2),
        ("Subtotal: 9,800 JPY", 0.60, 2),
        ("Total:", 0.62, 2),
        ("OK.", 0.60, 2),
    ]
    y = 72
    for text, scale, thickness in lines:
        cv2.putText(
            doc,
            text,
            (38, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            (25, 25, 25),
            thickness,
            cv2.LINE_AA,
        )
        y += int(round(58 * scale))
    return doc


def _paste_rotated(
    canvas: np.ndarray,
    doc: np.ndarray,
    *,
    center: tuple[float, float],
    angle_deg: float,
) -> None:
    ch, cw = canvas.shape[:2]
    dh, dw = doc.shape[:2]
    mat = cv2.getRotationMatrix2D((dw * 0.5, dh * 0.5), -angle_deg, 1.0)
    mat[0, 2] += center[0] - dw * 0.5
    mat[1, 2] += center[1] - dh * 0.5
    mask = np.full((dh, dw), 255, dtype=np.uint8)
    warped_mask = cv2.warpAffine(mask, mat, (cw, ch), flags=cv2.INTER_NEAREST, borderValue=0)
    warped_doc = cv2.warpAffine(
        doc, mat, (cw, ch), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    fg = warped_mask > 0
    canvas[fg] = warped_doc[fg]


def _heuristic_corrector() -> AutoOrientationCorrector:
    return AutoOrientationCorrector(
        config=OrientationConfig(prefer_native_backend=False, min_confidence=0.2),
        backends=[HeuristicOrientationBackend()],
    )


def test_three_differently_oriented_docs_saved_upright_as_three_sequential_files(
    tmp_path: Path,
) -> None:
    """One scan with tilted, 90° sideways, and 180° upside-down docs saves 3 upright files."""
    canvas = np.full((1700, 1800, 3), (26, 26, 28), dtype=np.uint8)

    doc1 = _render_portrait_letter_doc("DOC ONE")  # 0° upright + tilted +8.5°
    doc2 = rotate_image_cw(_render_portrait_letter_doc("DOC TWO"), 90)  # 90° CW + tilted -6.0°
    doc3 = rotate_image_cw(_render_portrait_letter_doc("DOC THREE"), 180)  # 180° + tilted +11.2°

    _paste_rotated(canvas, doc1, center=(420.0, 460.0), angle_deg=8.5)
    _paste_rotated(canvas, doc2, center=(1280.0, 440.0), angle_deg=-6.0)
    _paste_rotated(canvas, doc3, center=(880.0, 1240.0), angle_deg=11.2)

    raw_scan_bytes = encode_image_bytes(canvas, fmt="JPEG", quality=94)

    platen, a3 = _make_platen_and_a3()
    mock_client = MagicMock()
    mock_client.scan_single_page.side_effect = lambda req: EsclScanResult(
        image_bytes=raw_scan_bytes,
        content_type="image/jpeg",
        job_url="http://192.168.100.13:80/eSCL/ScanJobs/1",
        request=req,
    )

    corrector = _heuristic_corrector()
    pipeline = MultiCropScanPipeline(
        MultiCropPipelineConfig(
            auto_crop_enabled=True,
            multi_document_enabled=True,
            auto_orientation_enabled=True,
            target_dpi=300.0,
        ),
        orientation_corrector=corrector,
    )

    profile = ScanSaveProfile(
        base_url="http://192.168.100.13:80/eSCL",
        source_caps=platen,
        paper_size=a3,
        save_dir=tmp_path,
        naming_template="Scan_{date}_{seq:03d}",
        file_format="PNG",
        resolution_dpi=300,
        post_processor=pipeline.as_post_processor(),
    )
    session = ContinuousScanSession(profile, scan_client_factory=lambda _url: mock_client)

    step = session.scan_and_save_next(now=FIXED_NOW)

    assert len(step.saved_results) == 3
    assert step.total_saved_files == 3
    assert step.next_seq == 4

    expected_names = [
        "Scan_20261118_001.png",
        "Scan_20261118_002.png",
        "Scan_20261118_003.png",
    ]
    for saved, exp_name in zip(step.saved_results, expected_names, strict=True):
        assert saved.output_path == tmp_path / exp_name
        assert saved.output_path.is_file()

        # Verify each saved file is upright portrait (height > width) and already at 0°!
        with Image.open(saved.output_path) as img:
            w, h = img.size
            assert h > w
            bgr = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
            recheck = corrector.detect_orientation(bgr)
            assert recheck.rotation_cw_deg == 0


def test_preview_confirmed_boxes_scale_to_main_scan_and_continuous_second_scan_redetects(
    tmp_path: Path,
) -> None:
    """Preview manual tweaks apply on 1st main scan; 2nd continuous scan auto-detects new layout."""
    # Scan 1: 2 documents at 100 DPI preview and 300 DPI (3x) main scan.
    preview_canvas = np.full((600, 800, 3), (28, 28, 30), dtype=np.uint8)
    cv2.rectangle(preview_canvas, (60, 100), (310, 480), (245, 245, 242), thickness=-1)
    cv2.rectangle(preview_canvas, (450, 140), (740, 460), (242, 244, 248), thickness=-1)

    main_canvas_1 = cv2.resize(preview_canvas, (2400, 1800), interpolation=cv2.INTER_CUBIC)

    # Detect on preview and apply user manual tweaks: -2.0mm margin & manual 90° CW on box 1.
    detector = MultiDocumentDetector()
    preview_boxes = detector.detect(preview_canvas, dpi=100.0)
    assert len(preview_boxes) == 2
    tweaked_boxes = [
        preview_boxes[0].with_uniform_margin(-2.0, unit="mm").with_manual_rotation(90),
        preview_boxes[1].with_uniform_margin(-2.0, unit="mm").with_manual_rotation(0),
    ]

    # Scan 2 (continuous scan): freshly placed 2 documents in different positions.
    main_canvas_2 = np.full((1800, 2400, 3), (28, 28, 30), dtype=np.uint8)
    cv2.rectangle(main_canvas_2, (200, 200), (900, 800), (240, 242, 245), thickness=-1)
    cv2.rectangle(main_canvas_2, (1300, 900), (2100, 1600), (245, 240, 238), thickness=-1)

    platen, a3 = _make_platen_and_a3()
    mock_client = MagicMock()
    mock_client.scan_single_page.side_effect = [
        EsclScanResult(
            encode_image_bytes(main_canvas_1, fmt="JPEG", quality=92),
            "image/jpeg",
            "u1",
            EsclScanRequest(3507, 4960, resolution_dpi=300),
        ),
        EsclScanResult(
            encode_image_bytes(main_canvas_2, fmt="JPEG", quality=92),
            "image/jpeg",
            "u2",
            EsclScanRequest(3507, 4960, resolution_dpi=300),
        ),
    ]

    pipeline = MultiCropScanPipeline(
        MultiCropPipelineConfig(
            auto_crop_enabled=True,
            multi_document_enabled=True,
            auto_orientation_enabled=False,
            target_dpi=300.0,
        ),
        orientation_corrector=_heuristic_corrector(),
    )
    profile = ScanSaveProfile(
        base_url="http://192.168.100.13:80/eSCL",
        source_caps=platen,
        paper_size=a3,
        save_dir=tmp_path,
        naming_template="Batch_{seq:03d}",
        file_format="JPEG",
        resolution_dpi=300,
        quality=90,
        post_processor=pipeline.as_post_processor(
            preview_boxes=tweaked_boxes,
            consume_preview_boxes_once=True,
        ),
    )
    session = ContinuousScanSession(profile, scan_client_factory=lambda _url: mock_client)

    step1 = session.scan_and_save_next(now=FIXED_NOW)
    assert [r.output_path.name for r in step1.saved_results] == ["Batch_001.jpg", "Batch_002.jpg"]
    assert step1.next_seq == 3

    # Box 1 was portrait (250x380 -> 750x1140 at 3x), minus ~47px for -2mm each side,
    # then rotated 90° CW -> landscape (width > height)!
    with Image.open(tmp_path / "Batch_001.jpg") as img1:
        w1, h1 = img1.size
        assert w1 > h1
        assert 1050 < w1 < 1130
        assert 670 < h1 < 740

    # Continuous scan 2nd batch re-detects new layout and keeps unbroken sequence 003, 004.
    step2 = session.scan_and_save_next(now=FIXED_NOW)
    assert [r.output_path.name for r in step2.saved_results] == ["Batch_003.jpg", "Batch_004.jpg"]
    assert step2.total_saved_files == 4
    assert step2.next_seq == 5


def test_multipage_pdf_mode_combines_multi_crop_across_continuous_scans(tmp_path: Path) -> None:
    """Multi-page PDF mode buffers all cropped pages across consecutive scans into one PDF."""
    scan1 = np.full((900, 1200, 3), (25, 25, 25), dtype=np.uint8)
    cv2.rectangle(scan1, (80, 120), (480, 760), (245, 245, 245), thickness=-1)
    cv2.rectangle(scan1, (640, 120), (1100, 760), (240, 242, 246), thickness=-1)

    scan2 = np.full((900, 1200, 3), (25, 25, 25), dtype=np.uint8)
    cv2.rectangle(scan2, (250, 120), (950, 780), (246, 244, 240), thickness=-1)

    platen, a3 = _make_platen_and_a3()
    mock_client = MagicMock()
    mock_client.scan_single_page.side_effect = [
        EsclScanResult(
            encode_image_bytes(scan1, fmt="JPEG"),
            "image/jpeg",
            "u1",
            EsclScanRequest(3507, 4960, resolution_dpi=300),
        ),
        EsclScanResult(
            encode_image_bytes(scan2, fmt="JPEG"),
            "image/jpeg",
            "u2",
            EsclScanRequest(3507, 4960, resolution_dpi=300),
        ),
    ]

    pipeline = MultiCropScanPipeline(
        MultiCropPipelineConfig(
            auto_crop_enabled=True,
            multi_document_enabled=True,
            auto_orientation_enabled=False,
            target_dpi=300.0,
        ),
        orientation_corrector=_heuristic_corrector(),
    )
    profile = ScanSaveProfile(
        base_url="http://192.168.100.13:80/eSCL",
        source_caps=platen,
        paper_size=a3,
        save_dir=tmp_path,
        naming_template="Bundle_{seq:02d}",
        file_format="PDF",
        resolution_dpi=300,
        multipage_pdf=True,
        post_processor=pipeline.as_post_processor(),
    )
    session = ContinuousScanSession(profile, scan_client_factory=lambda _url: mock_client)

    s1 = session.scan_and_save_next(now=FIXED_NOW)
    assert s1.buffered_page_count == 2
    s2 = session.scan_and_save_next(now=FIXED_NOW)
    assert s2.buffered_page_count == 3

    exported = session.finalize_multipage_pdf(now=FIXED_NOW)
    assert exported.output_path == tmp_path / "Bundle_01.pdf"
    assert exported.page_count == 3
    with pikepdf.open(exported.output_path) as pdf:
        assert len(pdf.pages) == 3


def test_auto_crop_disabled_keeps_full_page_and_applies_auto_orientation() -> None:
    upright = _render_portrait_letter_doc("FULL PAGE")
    upside_down = rotate_image_cw(upright, 180)

    pipeline = MultiCropScanPipeline(
        MultiCropPipelineConfig(
            auto_crop_enabled=False,
            auto_orientation_enabled=True,
            target_dpi=300.0,
        ),
        orientation_corrector=_heuristic_corrector(),
    )
    docs = pipeline.process_documents(upside_down)
    assert len(docs) == 1
    assert docs[0].rotation_cw_deg == 180
    assert np.array_equal(docs[0].image, upright)
    assert len(pipeline.last_processed_documents) == 1
    assert pipeline.last_processed_documents[0].rotation_cw_deg == 180


def test_landscape_a4_preview_auto_90_rotation_persists_into_saved_jpeg_and_pdf(
    tmp_path: Path,
) -> None:
    """Landscape-scanned A4 needing 90° CW rotation saves upright portrait JPEG and PDF files."""
    upright = _render_portrait_letter_doc("A4 NOTICE", width=560, height=780)
    # Sideways scan (rotated 270° CW / 90° CCW) -> needs 90° CW to stand upright.
    sideways = rotate_image_cw(upright, 270)
    assert sideways.shape[1] > sideways.shape[0]  # landscape input

    detector = MultiDocumentDetector()
    preview_boxes = detector.detect(sideways, dpi=100.0)
    assert len(preview_boxes) == 1
    preview_boxes = [preview_boxes[0].with_auto_rotation(90)]

    platen, a3 = _make_platen_and_a3()
    sideways_jpeg = encode_image_bytes(sideways, fmt="JPEG", quality=92)
    mock_client = MagicMock()
    mock_client.scan_single_page.side_effect = lambda req: EsclScanResult(
        image_bytes=sideways_jpeg,
        content_type="image/jpeg",
        job_url="http://192.168.100.13:80/eSCL/ScanJobs/1",
        request=req,
    )

    pipeline = MultiCropScanPipeline(
        MultiCropPipelineConfig(
            auto_crop_enabled=True,
            multi_document_enabled=True,
            auto_orientation_enabled=True,
            target_dpi=300.0,
        ),
        orientation_corrector=_heuristic_corrector(),
    )
    profile = ScanSaveProfile(
        base_url="http://192.168.100.13:80/eSCL",
        source_caps=platen,
        paper_size=a3,
        save_dir=tmp_path,
        naming_template="Rotated_{seq:03d}",
        file_format="JPEG",
        resolution_dpi=300,
        post_processor=pipeline.as_post_processor(
            preview_boxes=preview_boxes,
            consume_preview_boxes_once=True,
        ),
    )
    session = ContinuousScanSession(profile, scan_client_factory=lambda _url: mock_client)

    # 1st scan uses preview-confirmed 90° CW rotation.
    step1 = session.scan_and_save_next(now=FIXED_NOW)
    out1 = step1.saved_results[0].output_path
    with Image.open(out1) as saved1:
        assert saved1.height > saved1.width

    # 2nd continuous scan auto-detects and rotates 90° CW on the fresh scan too.
    step2 = session.scan_and_save_next(now=FIXED_NOW)
    out2 = step2.saved_results[0].output_path
    with Image.open(out2) as saved2:
        assert saved2.height > saved2.width


def test_pipeline_applies_bleed_through_removal_to_saved_documents() -> None:
    """`MultiCropScanPipeline` applies `BleedThroughConfig` to cropped & full-page outputs."""
    from open_escl_client.imaging.bleed_through import BleedThroughConfig

    img = np.full((500, 400, 3), (244, 246, 248), dtype=np.uint8)
    # Faint reverse bleed bar
    cv2.rectangle(img, (40, 80), (360, 110), (200, 202, 204), -1)
    # Crisp foreground text bar
    cv2.rectangle(img, (40, 220), (360, 250), (30, 30, 30), -1)

    pipeline_off = MultiCropScanPipeline(
        MultiCropPipelineConfig(
            auto_crop_enabled=False,
            auto_orientation_enabled=False,
            bleed_through=BleedThroughConfig(enabled=False, strength=0.8),
        )
    )
    docs_off = pipeline_off.process_documents(img)
    assert len(docs_off) == 1
    assert float(np.mean(docs_off[0].image[85:105, 60:340])) < 210.0

    pipeline_on = MultiCropScanPipeline(
        MultiCropPipelineConfig(
            auto_crop_enabled=False,
            auto_orientation_enabled=False,
            bleed_through=BleedThroughConfig(
                enabled=True, strength=0.85, whiten_background=False
            ),
        )
    )
    docs_on = pipeline_on.process_documents(img)
    assert len(docs_on) == 1
    # Bleed bar cleaned near paper base while foreground bar remains dark.
    assert float(np.mean(docs_on[0].image[85:105, 60:340])) >= 232.0
    assert float(np.mean(docs_on[0].image[225:245, 60:340])) < 45.0

