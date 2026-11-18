"""Unit tests for multi-document detection and high-precision fine deskew engine."""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from open_escl_client.imaging.multi_crop import (
    DeskewConfig,
    DeskewCropProcessor,
    DetectionConfig,
    MultiDocumentDetector,
    RotatedCropBox,
    decode_image_bytes,
    encode_image_bytes,
)


def _create_text_document(
    width: int,
    height: int,
    *,
    paper_bgr: tuple[int, int, int] = (245, 244, 240),
    border_bgr: tuple[int, int, int] | None = None,
    with_text_lines: bool = True,
) -> np.ndarray:
    """Create a synthetic rectangular paper document with crisp horizontal text-like rows."""
    doc = np.full((height, width, 3), paper_bgr, dtype=np.uint8)
    if border_bgr is not None:
        cv2.rectangle(doc, (0, 0), (width - 1, height - 1), border_bgr, thickness=2)

    if with_text_lines:
        margin_x = max(16, width // 10)
        margin_y = max(20, height // 10)
        line_step = max(14, height // 16)
        # Draw header rule and faux printed text lines (segmented horizontal strokes).
        cv2.line(
            doc,
            (margin_x, margin_y),
            (width - margin_x, margin_y),
            (40, 40, 40),
            thickness=2,
        )
        y = margin_y + line_step
        row_idx = 0
        while y < height - margin_y:
            x = margin_x
            end_x = width - margin_x - (20 if row_idx % 3 == 2 else 0)
            while x < end_x - 12:
                word_w = 18 + ((x + y) % 24)
                x2 = min(end_x, x + word_w)
                cv2.rectangle(doc, (x, y - 4), (x2, y + 1), (35, 35, 35), thickness=-1)
                x = x2 + 7
            y += line_step
            row_idx += 1
    return doc


def _paste_rotated_document(
    canvas: np.ndarray,
    doc: np.ndarray,
    *,
    center: tuple[float, float],
    angle_deg: float,
    shadow_delta: int = 0,
) -> None:
    """Paste `doc` onto `canvas` rotated clockwise by `angle_deg` around `center`.

    Positive `angle_deg` rotates the pasted document clockwise in image coordinates
    (matching `RotatedCropBox.angle_deg`).
    """
    ch, cw = canvas.shape[:2]
    dh, dw = doc.shape[:2]

    # Build source-to-canvas affine matrix: rotate clockwise by `angle_deg` (which is
    # `-angle_deg` in OpenCV's counter-clockwise `getRotationMatrix2D` convention).
    mat = cv2.getRotationMatrix2D((dw * 0.5, dh * 0.5), -angle_deg, 1.0)
    mat[0, 2] += center[0] - dw * 0.5
    mat[1, 2] += center[1] - dh * 0.5

    mask = np.full((dh, dw), 255, dtype=np.uint8)
    warped_mask = cv2.warpAffine(
        mask, mat, (cw, ch), flags=cv2.INTER_NEAREST, borderValue=0
    )

    if shadow_delta > 0:
        # Simulate subtle drop shadow around white paper edges on a white flatbed platen.
        shadow_mask = cv2.dilate(
            warped_mask,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)),
            iterations=1,
        )
        shadow_ring = cv2.subtract(shadow_mask, warped_mask) > 0
        canvas_f = canvas.astype(np.int16)
        canvas_f[shadow_ring] = np.clip(canvas_f[shadow_ring] - shadow_delta, 0, 255)
        canvas[:] = canvas_f.astype(np.uint8)

    warped_doc = cv2.warpAffine(
        doc, mat, (cw, ch), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    fg = warped_mask > 0
    canvas[fg] = warped_doc[fg]


def _measure_crop_horizontal_skew(crop: np.ndarray) -> float:
    """Independently measure residual horizontal skew of a cropped document."""
    processor = DeskewCropProcessor()
    residual, _ = processor.estimate_residual_angle(crop)
    return residual


def test_detect_and_deskew_multiple_rotated_docs_on_dark_background() -> None:
    canvas = np.full((1200, 1600, 3), (28, 28, 30), dtype=np.uint8)

    specs = [
        # (center, (w, h), true_angle_deg)
        ((380.0, 340.0), (340, 460), 12.3),
        ((1150.0, 360.0), (420, 300), -7.8),
        ((760.0, 880.0), (500, 320), 21.4),
    ]
    for center, (w, h), angle in specs:
        doc = _create_text_document(w, h, paper_bgr=(244, 242, 236))
        _paste_rotated_document(canvas, doc, center=center, angle_deg=angle)

    processor = DeskewCropProcessor()
    results = processor.process_image(canvas)

    assert len(results) == 3
    assert [r.box.index for r in results] == [1, 2, 3]

    for result, (expected_center, (exp_w, exp_h), expected_angle) in zip(
        results, specs, strict=True
    ):
        # Position accuracy
        assert abs(result.box.center_x - expected_center[0]) < 6.0
        assert abs(result.box.center_y - expected_center[1]) < 6.0

        # Angle accuracy (within 0.5 degrees; typically < 0.25 degrees)
        assert abs(result.refined_angle_deg - expected_angle) < 0.5

        # Output crop dimensions match original document dimensions closely
        crop_h, crop_w = result.image.shape[:2]
        assert abs(crop_w - exp_w) <= 8
        assert abs(crop_h - exp_h) <= 8

        # Residual skew after deskewing should be virtually zero
        residual_after = _measure_crop_horizontal_skew(result.image)
        assert abs(residual_after) < 0.4


def test_detect_multiple_docs_on_white_flatbed_background() -> None:
    """White platen (typical Brother flatbed) with cream paper, photo, and white sheet."""
    canvas = np.full((1100, 1500, 3), (248, 248, 248), dtype=np.uint8)

    # 1) Slightly warm/cream receipt paper tilted -9.4 deg
    receipt = _create_text_document(280, 440, paper_bgr=(234, 232, 222))
    _paste_rotated_document(
        canvas, receipt, center=(320.0, 520.0), angle_deg=-9.4, shadow_delta=18
    )

    # 2) Color photograph card tilted +14.6 deg
    photo = np.zeros((320, 420, 3), dtype=np.uint8)
    photo[:160, :] = (200, 140, 80)   # sky-like tone
    photo[160:, :] = (70, 130, 60)    # landscape tone
    cv2.rectangle(photo, (140, 90), (280, 230), (60, 80, 210), thickness=-1)
    _paste_rotated_document(
        canvas, photo, center=(1050.0, 330.0), angle_deg=14.6, shadow_delta=15
    )

    # 3) Nearly white document with subtle edge shadow tilted +4.2 deg
    white_doc = _create_text_document(
        440, 300, paper_bgr=(245, 245, 245), border_bgr=(212, 212, 212)
    )
    _paste_rotated_document(
        canvas, white_doc, center=(1030.0, 800.0), angle_deg=4.2, shadow_delta=22
    )

    processor = DeskewCropProcessor()
    results = processor.process_image(canvas)

    assert len(results) == 3

    # Match each detected result to its nearest ground truth by center distance.
    expected = [
        ((320.0, 520.0), -9.4),
        ((1050.0, 330.0), 14.6),
        ((1030.0, 800.0), 4.2),
    ]
    for exp_center, exp_angle in expected:
        nearest = min(
            results,
            key=lambda r: math.hypot(
                r.box.center_x - exp_center[0], r.box.center_y - exp_center[1]
            ),
        )
        dist = math.hypot(
            nearest.box.center_x - exp_center[0], nearest.box.center_y - exp_center[1]
        )
        assert dist < 12.0
        assert abs(nearest.refined_angle_deg - exp_angle) < 0.7


@pytest.mark.parametrize("injected_error", [2.3, -1.7, 0.9, -3.1])
def test_fine_deskew_refines_coarse_angle_error_within_sub_degree(
    injected_error: float,
) -> None:
    """When coarse box angle is off by several degrees, DeskewCropProcessor corrects it."""
    canvas = np.full((1000, 1000, 3), (30, 30, 30), dtype=np.uint8)
    true_angle = 8.4
    doc = _create_text_document(460, 620, paper_bgr=(245, 245, 242))
    _paste_rotated_document(canvas, doc, center=(500.0, 500.0), angle_deg=true_angle)

    noisy_box = RotatedCropBox(
        center_x=500.0,
        center_y=500.0,
        width=460.0,
        height=620.0,
        angle_deg=true_angle - injected_error,
        source_width=1000,
        source_height=1000,
    )

    processor = DeskewCropProcessor(config=DeskewConfig(refine_angle=True))
    cropped = processor.crop_box(canvas, noisy_box)

    assert abs(cropped.refined_angle_deg - true_angle) < 0.35
    assert abs(cropped.residual_angle_deg - injected_error) < 0.35
    assert cropped.angle_method in {"hough", "projection", "hybrid"}


def test_single_document_mode_returns_largest_only() -> None:
    canvas = np.full((900, 1200, 3), (25, 25, 25), dtype=np.uint8)
    small_doc = _create_text_document(220, 280)
    large_doc = _create_text_document(460, 580)
    _paste_rotated_document(canvas, small_doc, center=(260.0, 420.0), angle_deg=5.0)
    _paste_rotated_document(canvas, large_doc, center=(820.0, 450.0), angle_deg=-6.5)

    detector = MultiDocumentDetector(DetectionConfig(multi_document=False))
    boxes = detector.detect(canvas)

    assert len(boxes) == 1
    assert boxes[0].index == 1
    assert abs(boxes[0].center_x - 820.0) < 8.0
    assert abs(boxes[0].angle_deg - (-6.5)) < 1.0


@pytest.mark.parametrize("bg_color", [(250, 250, 250), (20, 20, 20)])
def test_blank_platen_returns_empty_list(bg_color: tuple[int, int, int]) -> None:
    canvas = np.full((800, 1000, 3), bg_color, dtype=np.uint8)
    detector = MultiDocumentDetector()
    assert detector.detect(canvas) == []


def test_downsampled_detection_maps_coordinates_back_to_source_resolution() -> None:
    canvas = np.full((1200, 1600, 3), (25, 25, 25), dtype=np.uint8)
    doc = _create_text_document(480, 640)
    _paste_rotated_document(canvas, doc, center=(840.0, 620.0), angle_deg=-11.2)

    detector = MultiDocumentDetector(DetectionConfig(detection_max_dimension=400))
    boxes = detector.detect(canvas)

    assert len(boxes) == 1
    box = boxes[0]
    assert box.source_width == 1600
    assert box.source_height == 1200
    assert abs(box.center_x - 840.0) < 8.0
    assert abs(box.center_y - 620.0) < 8.0
    assert abs(box.width - 480.0) < 12.0
    assert abs(box.height - 640.0) < 12.0


def test_bytes_and_grayscale_input_support_and_box_geometry() -> None:
    canvas = np.full((700, 900, 3), (30, 30, 30), dtype=np.uint8)
    doc = _create_text_document(320, 420)
    _paste_rotated_document(canvas, doc, center=(450.0, 350.0), angle_deg=6.0)

    png_bytes = encode_image_bytes(canvas, fmt="PNG")
    decoded = decode_image_bytes(png_bytes)
    assert decoded.shape == canvas.shape

    processor = DeskewCropProcessor()
    from_bytes = processor.process_image(png_bytes)
    assert len(from_bytes) == 1
    assert abs(from_bytes[0].refined_angle_deg - 6.0) < 0.5

    gray = cv2.cvtColor(canvas, cv2.COLOR_BGR2GRAY)
    from_gray = processor.process_image(gray)
    assert len(from_gray) == 1

    # Verify corners() geometry consistency
    box = from_bytes[0].box
    corners = box.corners()
    assert corners.shape == (4, 2)
    bx, by, bw, bh = box.bounding_rect()
    assert bw >= box.width * 0.95
    assert bh >= box.height * 0.95
    assert bx < box.center_x < bx + bw
    assert by < box.center_y < by + bh


def test_detect_multiple_docs_with_realistic_scanner_noise() -> None:
    """Realistic sensor noise on white platen should not cause false detections."""
    rng = np.random.default_rng(42)
    base = np.full((1000, 1400, 3), 246.0, dtype=np.float32)
    noise = rng.normal(0.0, 2.5, size=base.shape).astype(np.float32)
    canvas = np.clip(base + noise, 0, 255).astype(np.uint8)

    doc1 = _create_text_document(340, 460, paper_bgr=(230, 228, 220))
    doc2 = _create_text_document(420, 300, paper_bgr=(226, 232, 238))
    _paste_rotated_document(canvas, doc1, center=(360.0, 490.0), angle_deg=6.7, shadow_delta=14)
    _paste_rotated_document(canvas, doc2, center=(1020.0, 520.0), angle_deg=-13.4, shadow_delta=14)

    processor = DeskewCropProcessor()
    results = processor.process_image(canvas)

    assert len(results) == 2
    assert abs(results[0].refined_angle_deg - 6.7) < 0.6
    assert abs(results[1].refined_angle_deg - (-13.4)) < 0.6


@pytest.mark.parametrize("fine_skew", [0.4, -0.7, 1.3, -2.2])
def test_sub_degree_projection_angle_refinement_without_outer_edges(
    fine_skew: float,
) -> None:
    """Internal text lines alone allow 0.1-degree-scale skew estimation."""
    doc = _create_text_document(600, 800, paper_bgr=(245, 245, 245))
    h, w = doc.shape[:2]
    mat = cv2.getRotationMatrix2D((w * 0.5, h * 0.5), -fine_skew, 1.0)
    tilted = cv2.warpAffine(
        doc, mat, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )

    processor = DeskewCropProcessor()
    residual, method = processor.estimate_residual_angle(tilted)

    assert method in {"hough", "projection", "hybrid"}
    assert abs(residual - fine_skew) < 0.35


def test_full_page_white_a4_with_inner_table_keeps_full_paper_bounds() -> None:
    """A4 full-page white scan with inner table must not shrink inward to the table box."""
    rng = np.random.default_rng(123)
    w, h = 827, 1169  # 100 DPI A4
    base = np.full((h, w, 3), (247.0, 246.0, 242.0), dtype=np.float32)
    noise = rng.normal(0.0, 1.8, size=base.shape).astype(np.float32)
    canvas = np.clip(base + noise, 0, 255).astype(np.uint8)

    # Printed header + shaded bordered table well inside A4 margins (like real Brother scan).
    cv2.putText(
        canvas,
        "MOVING NOTICE GUIDE",
        (90, 130),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.1,
        (25, 25, 25),
        3,
        cv2.LINE_AA,
    )
    # Fill table area with light gray shading (similar to government notice forms).
    cv2.rectangle(canvas, (85, 175), (w - 85, h - 110), (208, 212, 216), thickness=-1)
    cv2.rectangle(canvas, (85, 175), (w - 85, h - 110), (35, 35, 35), thickness=3)
    for row_y in range(255, h - 130, 80):
        cv2.line(canvas, (85, row_y), (w - 85, row_y), (55, 55, 55), thickness=2)
        cv2.putText(
            canvas,
            "Procedure details and required documents",
            (110, row_y - 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (35, 35, 35),
            2,
            cv2.LINE_AA,
        )

    detector = MultiDocumentDetector()
    boxes = detector.detect(canvas, dpi=100.0)

    assert len(boxes) == 1
    box = boxes[0]
    assert box.width == pytest.approx(float(w), abs=2.0)
    assert box.height == pytest.approx(float(h), abs=2.0)
    assert box.center_x == pytest.approx(w * 0.5, abs=2.0)
    assert box.center_y == pytest.approx(h * 0.5, abs=2.0)


def test_full_page_white_a4_with_separated_text_blocks_does_not_over_split() -> None:
    """Two separated printed blocks on one full-page white A4 sheet stay one full-page crop."""
    w, h = 827, 1169
    canvas = np.full((h, w, 3), (248, 248, 245), dtype=np.uint8)

    # Top title block and lower table block separated by blank white space.
    cv2.rectangle(canvas, (110, 90), (w - 110, 230), (30, 30, 30), thickness=3)
    cv2.rectangle(canvas, (110, 420), (w - 110, 1020), (30, 30, 30), thickness=3)
    for y in range(470, 1000, 60):
        cv2.line(canvas, (110, y), (w - 110, y), (45, 45, 45), thickness=2)

    detector = MultiDocumentDetector()
    boxes = detector.detect(canvas, dpi=100.0)

    assert len(boxes) == 1
    assert boxes[0].width == pytest.approx(float(w), abs=2.0)
    assert boxes[0].height == pytest.approx(float(h), abs=2.0)


def test_white_sheet_with_inner_table_on_larger_white_platen_keeps_outer_paper_edge() -> None:
    """A smaller white sheet with outer shadow edge + inner dark table crops at the sheet edge."""
    canvas = np.full((1200, 1600, 3), (249, 249, 249), dtype=np.uint8)

    sheet_w, sheet_h = 520, 720
    sheet = np.full((sheet_h, sheet_w, 3), (245, 245, 243), dtype=np.uint8)
    # Subtle outer sheet boundary + inner dark printed table separated by white paper margins.
    cv2.rectangle(sheet, (0, 0), (sheet_w - 1, sheet_h - 1), (212, 212, 212), thickness=2)
    cv2.rectangle(sheet, (65, 80), (sheet_w - 65, sheet_h - 80), (30, 30, 30), thickness=3)
    for y in range(150, sheet_h - 100, 65):
        cv2.line(sheet, (65, y), (sheet_w - 65, y), (45, 45, 45), thickness=2)

    _paste_rotated_document(
        canvas, sheet, center=(800.0, 600.0), angle_deg=7.5, shadow_delta=22
    )

    detector = MultiDocumentDetector()
    boxes = detector.detect(canvas, dpi=100.0)

    assert len(boxes) == 1
    box = boxes[0]
    # Must match outer paper sheet (~520x720), NOT shrink to inner table (~390x560).
    assert abs(box.width - sheet_w) <= 18.0
    assert abs(box.height - sheet_h) <= 18.0
    assert abs(box.angle_deg - 7.5) < 1.0


def _draw_thermal_receipt(
    canvas: np.ndarray,
    *,
    x0: int,
    y0: int,
    width: int = 160,
    height: int = 500,
    paper_bgr: tuple[int, int, int] = (252, 253, 253),
    edge_shadow_bgr: tuple[int, int, int] = (224, 224, 224),
) -> None:
    """Draw a white thermal receipt with logo, header, items, and barcode across blank gaps."""
    cv2.rectangle(canvas, (x0, y0), (x0 + width, y0 + height), paper_bgr, thickness=-1)
    # Subtle paper edge shadow on one side (typical flatbed cover shadow on thin thermal paper).
    cv2.line(canvas, (x0 + width, y0), (x0 + width, y0 + height), edge_shadow_bgr, thickness=2)

    # 1) Top store logo block
    cv2.circle(canvas, (x0 + 38, y0 + 52), 18, (35, 35, 35), thickness=-1)
    cv2.rectangle(
        canvas, (x0 + 66, y0 + 38), (x0 + width - 18, y0 + 64), (35, 35, 35), thickness=-1
    )

    # Blank gap (~26px), then 2) Store header text block
    for dy in (94, 112, 130, 148):
        cv2.rectangle(
            canvas, (x0 + 18, y0 + dy), (x0 + width - 22, y0 + dy + 6), (45, 45, 45), thickness=-1
        )

    # Blank gap (~28px), then 3) Itemized lines
    for dy in (184, 204, 224, 244, 264, 284):
        cv2.rectangle(
            canvas, (x0 + 18, y0 + dy), (x0 + width - 20, y0 + dy + 6), (40, 40, 40), thickness=-1
        )

    # Blank gap (~28px), then 4) Promo banner + barcode + QR block
    cv2.rectangle(
        canvas, (x0 + 14, y0 + 320), (x0 + width - 14, y0 + 362), (30, 30, 30), thickness=-1
    )
    for bx in range(x0 + 20, x0 + width - 20, 6):
        cv2.line(canvas, (bx, y0 + 378), (bx, y0 + 420), (30, 30, 30), thickness=2)
    cv2.rectangle(
        canvas, (x0 + width - 64, y0 + 432), (x0 + width - 18, y0 + 478), (30, 30, 30), thickness=-1
    )


def test_single_white_thermal_receipt_with_blank_gaps_detected_as_one_box() -> None:
    """One white thermal receipt with internal blank gaps must NOT split or expand to full A3."""
    canvas = np.full((800, 1100, 3), (254, 254, 254), dtype=np.uint8)
    _draw_thermal_receipt(canvas, x0=460, y0=130, width=160, height=500)

    detector = MultiDocumentDetector()
    boxes = detector.detect(canvas, dpi=100.0)

    assert len(boxes) == 1
    box = boxes[0]
    # Stay receipt-sized (not full 1100x800 platen) and cover from top logo to bottom QR.
    assert 130.0 <= box.width <= 220.0
    assert 430.0 <= box.height <= 560.0
    assert abs(box.center_x - 540.0) <= 25.0
    assert abs(box.center_y - 380.0) <= 30.0


def test_multiple_white_and_colored_receipts_on_white_platen_detected_individually() -> None:
    """Platen with a white receipt, white slip, and colored receipt detects exactly 3 boxes."""
    canvas = np.full((800, 1100, 3), (254, 254, 254), dtype=np.uint8)

    # 1) White guarantee slip on the left (title + body separated by blank space)
    cv2.rectangle(canvas, (150, 260), (290, 286), (35, 35, 35), thickness=-1)
    for dy in (314, 336, 358, 380, 412, 434):
        cv2.rectangle(canvas, (110, dy), (320, dy + 8), (45, 45, 45), thickness=-1)

    # 2) Long white thermal receipt in the center
    _draw_thermal_receipt(canvas, x0=470, y0=120, width=155, height=500)

    # 3) Light blue thermal receipt on the right
    cv2.rectangle(canvas, (770, 240), (930, 560), (232, 232, 203), thickness=-1)
    for dy in range(270, 530, 26):
        cv2.rectangle(canvas, (790, dy), (910, dy + 6), (40, 40, 40), thickness=-1)

    detector = MultiDocumentDetector()
    boxes = detector.detect(canvas, dpi=100.0)

    assert len(boxes) == 3
    # Verify center receipt covers the full height from logo to QR (~440..560px tall).
    center_box = min(boxes, key=lambda b: abs(b.center_x - 547.0))
    assert center_box.height >= 430.0
    assert center_box.width <= 220.0


def test_sideways_white_receipt_fragments_merge_along_horizontal_axis() -> None:
    """A 90°-rotated white thermal receipt merges horizontally aligned fragments into 1 box."""
    upright_canvas = np.full((800, 1100, 3), (254, 254, 254), dtype=np.uint8)
    _draw_thermal_receipt(upright_canvas, x0=460, y0=130, width=160, height=500)
    sideways = cv2.rotate(upright_canvas, cv2.ROTATE_90_COUNTERCLOCKWISE)

    detector = MultiDocumentDetector()
    boxes = detector.detect(sideways, dpi=100.0)

    assert len(boxes) == 1
    box = boxes[0]
    assert 430.0 <= box.width <= 560.0
    assert 130.0 <= box.height <= 220.0


def test_tangential_shadow_edge_tracing_expands_white_slip_to_physical_width() -> None:
    """Faint top shadow line past printed text expands box to true physical sheet width."""
    canvas = np.full((800, 1000, 3), (254, 254, 254), dtype=np.uint8)

    # Physical white guarantee slip: x=[240..560] (width=320), y=[320..460] (height=140).
    # Printed text occupies only the inner x=[295..505] (width=210), leaving 55px white margins.
    slip_x1, slip_x2, slip_y1 = 240, 560, 320
    # Subtle top-edge shadow line with a small 4px fade gap to test gap tolerance.
    cv2.line(canvas, (slip_x1, slip_y1), (360, slip_y1), (228, 228, 228), thickness=2)
    cv2.line(canvas, (365, slip_y1), (slip_x2, slip_y1), (228, 228, 228), thickness=2)

    # Printed heading & body strictly inside [295..505]
    cv2.rectangle(canvas, (355, 342), (445, 362), (35, 35, 35), thickness=-1)
    for dy in (378, 396, 414, 432):
        cv2.rectangle(canvas, (295, dy), (505, dy + 6), (45, 45, 45), thickness=-1)

    detector = MultiDocumentDetector()
    boxes = detector.detect(canvas, dpi=100.0)

    assert len(boxes) == 1
    box = boxes[0]
    # Must trace the top shadow line out to the physical width (~320px), not stop at ~210px.
    assert 304.0 <= box.width <= 336.0
    assert abs(box.center_x - 400.0) <= 12.0


def test_tangential_shadow_tracing_unifies_separated_label_and_value_columns() -> None:
    """Separated left label column and right heading/value column unify under shared shadow."""
    canvas = np.full((760, 620, 3), (254, 254, 254), dtype=np.uint8)

    # Top shadow line spanning x=[180..480] (width=300) at y=620 (34px above heading,
    # mirroring 100 DPI real-device header margin when bottom of slip is clipped).
    cv2.line(canvas, (180, 620), (480, 620), (226, 226, 226), thickness=2)
    # Right heading + value block (x=[300..420], height ~72px)
    cv2.rectangle(canvas, (315, 654), (405, 676), (35, 35, 35), thickness=-1)
    cv2.rectangle(canvas, (302, 692), (420, 704), (45, 45, 45), thickness=-1)
    cv2.rectangle(canvas, (302, 714), (420, 726), (45, 45, 45), thickness=-1)
    # Separated left label column (x=[225..265], 37px gap to right column)
    cv2.rectangle(canvas, (225, 692), (265, 704), (45, 45, 45), thickness=-1)
    cv2.rectangle(canvas, (225, 714), (265, 726), (45, 45, 45), thickness=-1)

    detector = MultiDocumentDetector()
    boxes = detector.detect(canvas, dpi=100.0)

    assert len(boxes) == 1
    box = boxes[0]
    bx, _, bw, _ = box.bounding_rect()
    # Both the left label column (x=225) and the full traced width (~300px) must be included.
    assert bx <= 200.0
    assert (bx + bw) >= 460.0


def test_tangential_shadow_tracing_stops_before_adjacent_document() -> None:
    """Shadow tracing must not cross into an adjacent document box."""
    canvas = np.full((800, 1000, 3), (254, 254, 254), dtype=np.uint8)

    # White slip on left with faint top shadow line extending toward a nearby colored card.
    cv2.line(canvas, (140, 320), (452, 320), (232, 232, 232), thickness=2)
    cv2.rectangle(canvas, (200, 345), (360, 440), (40, 40, 40), thickness=3)
    for dy in (365, 390, 415):
        cv2.line(canvas, (210, dy), (350, dy), (40, 40, 40), thickness=2)

    # Colored card nearby to the right at x=[470..670]
    cv2.rectangle(canvas, (470, 260), (670, 500), (220, 185, 145), thickness=-1)

    detector = MultiDocumentDetector()
    boxes = detector.detect(canvas, dpi=100.0)

    assert len(boxes) == 2
    left_box = min(boxes, key=lambda b: b.center_x)
    lx, _, lw, _ = left_box.bounding_rect()
    # Right edge of left box must stop before colliding into the colored card at x=470.
    assert (lx + lw) <= 470.0


def test_absorbed_top_shadow_line_snaps_tightly_without_overshoot() -> None:
    """When a strong paper-edge shadow line is absorbed into the raw contour, snap onto it."""
    canvas = np.full((900, 1100, 3), (254, 254, 254), dtype=np.uint8)

    # Horizontal receipt with crisp top shadow line at y=350 (gray=185) and printed text y=362..520.
    cv2.line(canvas, (180, 350), (880, 350), (185, 185, 185), thickness=2)
    for x in range(200, 860, 45):
        cv2.rectangle(canvas, (x, 366), (x + 28, 520), (40, 40, 40), thickness=-1)

    detector = MultiDocumentDetector()
    boxes = detector.detect(canvas, dpi=100.0)

    assert len(boxes) == 1
    _, by, _, bh = boxes[0].bounding_rect()
    # Top edge must sit closely on the crisp shadow line y=350 (not overshoot upward to ~335).
    assert 346.0 <= by <= 354.0
    assert (by + bh) <= 536.0


def test_a4_sheet_on_a3_white_platen_crops_to_a4_bounds_and_auto_selects_standard_sheet() -> None:
    """An A4 white sheet scanned under A3 platen settings crops to the A4 sheet, not full A3."""
    w, h = 717, 1024  # 100 DPI A3 platen
    canvas = np.full((h, w, 3), (254, 254, 254), dtype=np.uint8)

    # Physical A4 sheet placed at x=[81..592] (w=511), y=[83..809] (h=726) with slightly
    # off-white paper luminance (~251) and a faint top paper-edge shadow line at y=83.
    a4_x1, a4_y1, a4_x2, a4_y2 = 81, 83, 592, 809
    cv2.rectangle(canvas, (a4_x1, a4_y1), (a4_x2, a4_y2), (251, 251, 251), thickness=-1)
    cv2.line(canvas, (a4_x1, a4_y1), (a4_x2, a4_y1), (238, 238, 238), thickness=2)

    # Printed heading (y=121..138) + bordered shaded table (x=98..576, y=150..752) inside A4.
    cv2.rectangle(canvas, (130, 121), (540, 138), (30, 30, 30), thickness=-1)
    cv2.rectangle(canvas, (98, 150), (576, 752), (232, 232, 232), thickness=-1)
    cv2.rectangle(canvas, (98, 150), (576, 752), (35, 35, 35), thickness=2)
    for row_y in range(195, 740, 42):
        cv2.line(canvas, (98, row_y), (576, row_y), (50, 50, 50), thickness=1)

    # 1) Default `auto` preset must NOT expand to full A3 (717x1024); it must auto-select
    #    `standard_sheet` and crop to the A4 physical boundaries (~511x726).
    auto_detector = MultiDocumentDetector()
    auto_boxes = auto_detector.detect(canvas, dpi=100.0)
    assert auto_detector.last_resolved_preset == "standard_sheet"
    assert len(auto_boxes) == 1
    bx, by, bw, bh = auto_boxes[0].bounding_rect()
    assert abs(bx - a4_x1) <= 6.0
    assert abs(by - a4_y1) <= 6.0
    assert abs(bw - (a4_x2 - a4_x1)) <= 10.0
    assert abs(bh - (a4_y2 - a4_y1)) <= 10.0

    # 2) Explicit `standard_sheet` preset produces the same A4 physical bounds.
    std_detector = MultiDocumentDetector(DetectionConfig(detection_preset="standard_sheet"))
    std_boxes = std_detector.detect(canvas, dpi=100.0)
    assert std_detector.last_resolved_preset == "standard_sheet"
    assert len(std_boxes) == 1
    assert abs(std_boxes[0].width - auto_boxes[0].width) <= 2.0
    assert abs(std_boxes[0].height - auto_boxes[0].height) <= 2.0

    # 3) Explicit `full_page` preset returns the full A3 scan frame when requested by user.
    full_detector = MultiDocumentDetector(DetectionConfig(detection_preset="full_page"))
    full_boxes = full_detector.detect(canvas, dpi=100.0)
    assert full_detector.last_resolved_preset == "full_page"
    assert len(full_boxes) == 1
    assert full_boxes[0].width == pytest.approx(float(w), abs=1.0)
    assert full_boxes[0].height == pytest.approx(float(h), abs=1.0)


def test_auto_preset_preserves_exact_receipt_multi_boxes() -> None:
    """`auto` preset selects `receipt_multi` on multi-receipt scans with zero difference."""
    canvas = np.full((720, 1024, 3), (254, 254, 254), dtype=np.uint8)
    _draw_thermal_receipt(canvas, x0=200, y0=130, width=155, height=460)
    _draw_thermal_receipt(canvas, x0=560, y0=150, width=160, height=440)

    auto_det = MultiDocumentDetector(DetectionConfig(detection_preset="auto"))
    receipt_det = MultiDocumentDetector(DetectionConfig(detection_preset="receipt_multi"))

    auto_boxes = auto_det.detect(canvas, dpi=100.0)
    receipt_boxes = receipt_det.detect(canvas, dpi=100.0)

    assert auto_det.last_resolved_preset == "receipt_multi"
    assert receipt_det.last_resolved_preset == "receipt_multi"
    assert len(auto_boxes) == 2
    assert auto_boxes == receipt_boxes


def test_bordered_postcard_and_text_postcard_crop_tightly_without_platen_halo() -> None:
    """Two tilted postcards on a white platen do not trigger full-page or leave outer halo."""
    w, h = 717, 1024
    canvas = np.full((h, w, 3), (254, 254, 254), dtype=np.uint8)

    # 1) Upper postcard (340x252) with outer shadow line, narrow 2px white rim, and blue frame.
    card1_w, card1_h = 340, 252
    card1 = np.full((card1_h, card1_w, 3), (252, 252, 252), dtype=np.uint8)
    # 1px paper-edge shadow around perimeter, 2px white rim, then dense colored border frame.
    cv2.rectangle(card1, (0, 0), (card1_w - 1, card1_h - 1), (232, 232, 232), thickness=1)
    cv2.rectangle(card1, (3, 3), (card1_w - 4, card1_h - 4), (195, 165, 120), thickness=14)
    cv2.rectangle(card1, (18, 18), (card1_w - 19, 48), (45, 40, 185), thickness=-1)
    for y in range(70, card1_h - 25, 22):
        cv2.rectangle(card1, (26, y), (card1_w - 26, y + 8), (60, 50, 40), thickness=-1)
    _paste_rotated_document(canvas, card1, center=(382.0, 280.0), angle_deg=12.75, shadow_delta=0)

    # 2) Lower white text postcard (350x230) tilted at -5.2 deg.
    card2_w, card2_h = 350, 230
    card2 = np.full((card2_h, card2_w, 3), (254, 254, 254), dtype=np.uint8)
    cv2.line(card2, (0, 0), (card2_w - 1, 0), (220, 220, 220), thickness=2)
    for y in range(28, card2_h - 20, 20):
        cv2.rectangle(card2, (24, y), (card2_w - 24, y + 7), (40, 40, 40), thickness=-1)
    _paste_rotated_document(canvas, card2, center=(358.0, 726.0), angle_deg=-5.2, shadow_delta=0)

    detector = MultiDocumentDetector()
    boxes = detector.detect(canvas, dpi=100.0)

    # Must NOT collapse into 1 full-page box; must detect both postcards individually.
    assert detector.last_resolved_preset == "receipt_multi"
    assert len(boxes) == 2

    upper_box = min(boxes, key=lambda b: b.center_y)
    # Upper bordered postcard must be cropped tightly to its physical frame (~336x248),
    # without adding +18px of outer white platen margin.
    assert upper_box.width <= card1_w + 2.0
    assert upper_box.height <= card1_h + 2.0
    assert upper_box.width >= card1_w - 10.0
    assert upper_box.height >= card1_h - 10.0



