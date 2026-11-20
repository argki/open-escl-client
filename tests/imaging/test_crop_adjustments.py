"""Unit and boundary tests for crop margin adjustments, manual tweaks, and DPI scaling."""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from open_escl_client.imaging.multi_crop import (
    MIN_CROP_SIDE_PX,
    CropMarginOffsets,
    DeskewCropProcessor,
    NormalizedCropBox,
    RotatedCropBox,
    mm_to_px,
    px_to_mm,
    scale_crop_box,
    scale_crop_boxes,
)


def test_mm_px_conversions_and_margin_offsets() -> None:
    assert abs(mm_to_px(25.4, 300.0) - 300.0) < 1e-6
    assert abs(px_to_mm(150.0, 300.0) - 12.7) < 1e-6

    with pytest.raises(ValueError):
        mm_to_px(1.0, 0.0)
    with pytest.raises(ValueError):
        px_to_mm(1.0, -100.0)

    m_mm = CropMarginOffsets.uniform(-1.5, unit="mm")
    m_px_300 = m_mm.to_pixels(300.0)
    assert m_px_300.unit == "px"
    assert abs(m_px_300.left - (-1.5 * 300.0 / 25.4)) < 1e-6
    assert m_mm.scaled(3.0) == m_mm  # Physical mm does not change when scaling resolution

    m_px = CropMarginOffsets(top=-2.0, right=4.0, bottom=-1.0, left=3.0, unit="px")
    scaled = m_px.scaled(3.0, 2.0)
    assert scaled == CropMarginOffsets(top=-4.0, right=12.0, bottom=-2.0, left=9.0, unit="px")


def test_negative_margin_trims_edge_shadow_and_positive_margin_expands() -> None:
    """Negative mm/px margins cleanly trim faint border shadows; positive margins expand."""
    canvas = np.full((600, 600, 3), (40, 40, 40), dtype=np.uint8)
    # 300x400 white document with a 6px dark gray shadow rim around its perimeter.
    doc = np.full((400, 300, 3), (245, 245, 245), dtype=np.uint8)
    cv2.rectangle(doc, (0, 0), (299, 399), (120, 120, 120), thickness=12)  # 6px inward
    canvas[100:500, 150:450] = doc

    box = RotatedCropBox(
        center_x=300.0,
        center_y=300.0,
        width=300.0,
        height=400.0,
        angle_deg=0.0,
        source_width=600,
        source_height=600,
        source_dpi=254.0,  # 10 px per mm
    )
    processor = DeskewCropProcessor()

    # Negative margin: -1.0 mm == -10 px on every side -> trims away the 6px dark rim.
    trimmed_box = box.with_uniform_margin(-1.0, unit="mm")
    trimmed = processor.crop_box(canvas, trimmed_box, refine_angle=False)
    assert trimmed.image.shape[:2] == (380, 280)
    # All pixels in trimmed crop should be pure paper white (~245), no rim left.
    assert int(np.min(trimmed.image)) >= 240

    # Positive margin: +1.5 mm == +15 px on every side -> expands into dark platen background.
    expanded_box = box.with_uniform_margin(1.5, unit="mm")
    expanded = processor.crop_box(canvas, expanded_box, refine_angle=False)
    assert expanded.image.shape[:2] == (430, 330)
    assert int(expanded.image[2, 2, 0]) <= 50


def test_asymmetric_edge_offsets_preserve_untouched_edges_when_rotated() -> None:
    """Trimming only the left edge of a rotated box must keep right/top/bottom edges fixed."""
    base = RotatedCropBox(
        center_x=400.0,
        center_y=350.0,
        width=200.0,
        height=300.0,
        angle_deg=30.0,
        source_width=800,
        source_height=700,
    )
    base_corners = base.corners()  # TL(0), TR(1), BR(2), BL(3)

    # Trim left edge inward by 20px, leave top/right/bottom untouched.
    adjusted = base.with_edge_margins(left=-20.0, unit="px")
    adj_corners = adjusted.corners()

    # TR and BR (the right edge) must remain at identical image coordinates.
    assert np.allclose(adj_corners[1], base_corners[1], atol=1e-4)
    assert np.allclose(adj_corners[2], base_corners[2], atol=1e-4)

    # TL and BL must move strictly inward along the box's rotated +X direction by 20px.
    rad = math.radians(30.0)
    expected_shift = np.array([20.0 * math.cos(rad), 20.0 * math.sin(rad)], dtype=np.float32)
    assert np.allclose(adj_corners[0] - base_corners[0], expected_shift, atol=1e-4)
    assert np.allclose(adj_corners[3] - base_corners[3], expected_shift, atol=1e-4)


def test_excessive_negative_margin_clamps_safely_without_inverting() -> None:
    """Negative margins exceeding box dimensions clamp cleanly to `MIN_CROP_SIDE_PX`."""
    box = RotatedCropBox(
        center_x=250.0,
        center_y=250.0,
        width=100.0,
        height=80.0,
        angle_deg=15.0,
        source_width=500,
        source_height=500,
    )
    extreme = box.with_uniform_margin(-120.0, unit="px")
    cx, cy, w, h, angle = extreme.resolved_geometry()

    assert w == pytest.approx(MIN_CROP_SIDE_PX, abs=1e-5)
    assert h == pytest.approx(MIN_CROP_SIDE_PX, abs=1e-5)
    assert cx == pytest.approx(250.0, abs=1e-5)
    assert cy == pytest.approx(250.0, abs=1e-5)
    assert angle == pytest.approx(15.0, abs=1e-5)


@pytest.mark.parametrize("corner_idx", [0, 1, 2, 3])
def test_corner_drag_fixes_opposite_corner_on_rotated_box(corner_idx: int) -> None:
    """Dragging any corner of a rotated box keeps the diagonal anchor fixed."""
    box = RotatedCropBox(
        center_x=500.0,
        center_y=400.0,
        width=240.0,
        height=320.0,
        angle_deg=-22.5,
        source_width=1000,
        source_height=800,
    )
    before = box.corners()
    opposite_idx = (corner_idx + 2) % 4
    anchor_before = before[opposite_idx]

    # Pull dragged corner 30px outward from box center.
    orig_pt = before[corner_idx]
    outward = orig_pt - np.array([box.center_x, box.center_y], dtype=np.float32)
    outward = outward / np.linalg.norm(outward) * 30.0
    target_pt = orig_pt + outward

    resized = box.with_corner_drag(corner_idx, float(target_pt[0]), float(target_pt[1]))
    after = resized.corners()

    assert np.allclose(after[opposite_idx], anchor_before, atol=1e-3)
    assert np.allclose(after[corner_idx], target_pt, atol=1e-3)


def test_corner_drag_past_opposite_corner_clamps_to_min_size() -> None:
    box = RotatedCropBox(
        center_x=200.0,
        center_y=200.0,
        width=100.0,
        height=100.0,
        angle_deg=0.0,
        source_width=400,
        source_height=400,
    )
    # Drag TL (150, 150) way past BR (250, 250) to (350, 350).
    clamped = box.with_corner_drag(0, 350.0, 350.0, min_size_px=16.0)
    assert clamped.width == pytest.approx(16.0, abs=1e-5)
    assert clamped.height == pytest.approx(16.0, abs=1e-5)


def test_manual_angle_and_90_deg_rotation_overrides() -> None:
    box = RotatedCropBox(
        center_x=300.0,
        center_y=300.0,
        width=200.0,
        height=260.0,
        angle_deg=5.0,
        source_width=600,
        source_height=600,
    )
    assert box.effective_rotation_cw_deg == 0

    with_auto = box.with_auto_rotation(180)
    assert with_auto.effective_rotation_cw_deg == 180

    # Manual override wins over auto rotation.
    stepped = with_auto.rotate_manual_step(90)
    assert stepped.manual_rotation_cw_deg == 270
    assert stepped.effective_rotation_cw_deg == 270

    stepped2 = stepped.rotate_manual_step(90)
    assert stepped2.effective_rotation_cw_deg == 0

    # Clearing manual override reverts to auto rotation.
    cleared = stepped2.with_manual_rotation(None)
    assert cleared.effective_rotation_cw_deg == 180

    # Manual fine-angle tweak is preserved and respected by DeskewCropProcessor.
    tweaked = cleared.with_manual_angle_offset(-1.4)
    assert tweaked.effective_angle_deg == pytest.approx(3.6, abs=1e-6)

    canvas = np.full((600, 600, 3), (240, 240, 240), dtype=np.uint8)
    processor = DeskewCropProcessor()
    cropped = processor.crop_box(canvas, tweaked)
    assert cropped.angle_method == "manual"
    assert cropped.refined_angle_deg == pytest.approx(3.6, abs=1e-6)
    assert cropped.box.effective_rotation_cw_deg == 180


def test_preview_to_main_scan_dpi_scaling_preserves_margins_and_rotations() -> None:
    """Preview adjustments at 100 DPI map accurately onto a 300 DPI Brother main scan."""
    preview_w, preview_h, preview_dpi = 1152, 1693, 100.0
    main_w, main_h, main_dpi = 3456, 5079, 300.0  # Exact 3x resolution scale

    box1 = (
        RotatedCropBox(
            center_x=320.0,
            center_y=450.0,
            width=400.0,
            height=550.0,
            angle_deg=7.5,
            confidence=0.96,
            index=1,
            source_width=preview_w,
            source_height=preview_h,
            source_dpi=preview_dpi,
        )
        .with_uniform_margin(-1.5, unit="mm")
        .with_edge_drag("left", -4.0)
        .with_position_offset(5.0, -3.0)
        .with_manual_angle_offset(0.4)
        .with_auto_rotation(90)
    )
    box2 = (
        RotatedCropBox(
            center_x=820.0,
            center_y=1180.0,
            width=360.0,
            height=480.0,
            angle_deg=-11.2,
            confidence=0.92,
            index=2,
            source_width=preview_w,
            source_height=preview_h,
            source_dpi=preview_dpi,
        )
        .with_uniform_margin(2.0, unit="mm")
        .with_auto_rotation(180)
        .with_manual_rotation(270)
    )

    scaled_boxes = scale_crop_boxes(
        [box1, box2],
        target_width=main_w,
        target_height=main_h,
        target_dpi=main_dpi,
    )
    assert len(scaled_boxes) == 2

    s1, s2 = scaled_boxes
    # Base coordinates scaled by exact 3x.
    assert s1.center_x == pytest.approx(box1.center_x * 3.0, abs=1e-4)
    assert s1.center_y == pytest.approx(box1.center_y * 3.0, abs=1e-4)
    assert s1.width == pytest.approx(box1.width * 3.0, abs=1e-4)
    assert s1.height == pytest.approx(box1.height * 3.0, abs=1e-4)
    assert s1.source_dpi == main_dpi

    # Physical mm margin stays -1.5mm and resolves to 3x pixels at 300 DPI vs 100 DPI.
    assert s1.margin_mm == CropMarginOffsets.uniform(-1.5, unit="mm")
    assert s1.margin_px.left == pytest.approx(-12.0, abs=1e-4)

    _, _, prev_eff_w, prev_eff_h, prev_eff_a = box1.resolved_geometry()
    _, _, main_eff_w, main_eff_h, main_eff_a = s1.resolved_geometry()
    assert main_eff_w == pytest.approx(prev_eff_w * 3.0, abs=1e-3)
    assert main_eff_h == pytest.approx(prev_eff_h * 3.0, abs=1e-3)
    assert main_eff_a == pytest.approx(prev_eff_a, abs=1e-6)

    # Rotation settings and indices carry over intact.
    assert s1.index == 1
    assert s1.auto_rotation_cw_deg == 90
    assert s1.manual_rotation_cw_deg is None
    assert s1.effective_rotation_cw_deg == 90
    assert s1.manual_angle_offset_deg == pytest.approx(0.4, abs=1e-6)

    assert s2.index == 2
    assert s2.auto_rotation_cw_deg == 180
    assert s2.manual_rotation_cw_deg == 270
    assert s2.effective_rotation_cw_deg == 270
    assert s2.margin_mm == CropMarginOffsets.uniform(2.0, unit="mm")

    # Roundtrip normalization check and invalid-dimension guards.
    norm1 = box1.to_normalized()
    assert isinstance(norm1, NormalizedCropBox)
    assert scale_crop_box(
        box1, target_width=preview_w, target_height=preview_h, target_dpi=preview_dpi
    ).resolved_geometry() == pytest.approx(box1.resolved_geometry(), abs=1e-4)

    with pytest.raises(ValueError):
        NormalizedCropBox.from_crop_box(
            RotatedCropBox(10.0, 10.0, 50.0, 50.0, 0.0)
        )
    with pytest.raises(ValueError):
        norm1.to_crop_box(0, 100)


@pytest.mark.parametrize("corner_idx", [0, 1, 2, 3])
def test_corner_drag_with_locked_aspect_ratio_keeps_opposite_corner_and_ratio(
    corner_idx: int,
) -> None:
    """Locked corner drag fixes diagonal anchor and preserves box or target aspect ratio."""
    box = RotatedCropBox(
        center_x=500.0,
        center_y=400.0,
        width=200.0,
        height=300.0,
        angle_deg=18.0,
        source_width=1000,
        source_height=800,
    )
    before = box.corners()
    opposite_idx = (corner_idx + 2) % 4
    anchor_before = before[opposite_idx]
    orig_ratio = box.width / box.height

    # Pull corner asymmetrically (large horizontal delta, small vertical delta) with ratio lock.
    orig_pt = before[corner_idx]
    target_pt = np.array([orig_pt[0] + 90.0, orig_pt[1] - 15.0], dtype=np.float32)

    resized = box.with_corner_drag(
        corner_idx,
        float(target_pt[0]),
        float(target_pt[1]),
        lock_aspect_ratio=True,
    )
    after = resized.corners()
    assert np.allclose(after[opposite_idx], anchor_before, atol=1e-3)
    assert (resized.width / resized.height) == pytest.approx(orig_ratio, abs=1e-5)

    # Explicit target ratio (e.g., portrait ISO A/B: 1 / sqrt(2)) and min_size clamping.
    portrait_iso_ratio = 1.0 / math.sqrt(2.0)
    clamped = box.with_corner_drag(
        corner_idx,
        float(anchor_before[0]),
        float(anchor_before[1]),
        min_size_px=20.0,
        lock_aspect_ratio=True,
        target_aspect_ratio=portrait_iso_ratio,
    )
    assert clamped.width >= 20.0 - 1e-5
    assert clamped.height >= 20.0 - 1e-5
    assert (clamped.width / clamped.height) == pytest.approx(portrait_iso_ratio, abs=1e-5)


def test_oriented_aspect_ratio_for_preset() -> None:
    from open_escl_client.imaging.multi_crop import oriented_aspect_ratio_for_preset

    assert oriented_aspect_ratio_for_preset("auto", 100.0, 200.0) is None
    assert oriented_aspect_ratio_for_preset("free", 200.0, 100.0) is None
    assert oriented_aspect_ratio_for_preset("iso_a_b", 297.0, 210.0) == pytest.approx(
        math.sqrt(2.0), abs=1e-6
    )
    assert oriented_aspect_ratio_for_preset("iso_a_b", 210.0, 297.0) == pytest.approx(
        1.0 / math.sqrt(2.0), abs=1e-6
    )
    assert oriented_aspect_ratio_for_preset("postcard", 100.0, 148.0) == pytest.approx(
        100.0 / 148.0, abs=1e-6
    )
