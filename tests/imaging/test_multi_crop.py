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

