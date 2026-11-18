"""Multi-document detection and high-precision fine deskew / cropping engine.

Detects one or more arbitrarily rotated paper documents placed on a scanner platen
(light or dark background), refines each document's fine skew angle using edge lines
and internal text/structure alignment, and extracts straight rectangular crops using
single-pass subpixel affine warping.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

import cv2
import numpy as np

BackgroundMode = Literal["auto", "light", "dark"]
RotationDeg = Literal[0, 90, 180, 270]
MarginUnit = Literal["mm", "px"]
EdgeName = Literal["top", "right", "bottom", "left"]

MM_PER_INCH = 25.4
MIN_CROP_SIDE_PX = 4.0


def mm_to_px(mm: float, dpi: float) -> float:
    """Convert millimeters to pixels at the given DPI."""
    if dpi <= 0.0:
        raise ValueError(f"dpi must be positive, got {dpi}")
    return float(mm) * float(dpi) / MM_PER_INCH


def px_to_mm(px: float, dpi: float) -> float:
    """Convert pixels to millimeters at the given DPI."""
    if dpi <= 0.0:
        raise ValueError(f"dpi must be positive, got {dpi}")
    return float(px) * MM_PER_INCH / float(dpi)


def normalize_rotation_cw(angle_deg: int) -> RotationDeg:
    """Normalize an arbitrary integer degree angle to the nearest 90° CW step."""
    snapped = int(round(angle_deg / 90.0) * 90) % 360
    if snapped == 90:
        return 90
    if snapped == 180:
        return 180
    if snapped == 270:
        return 270
    return 0


def decode_image_bytes(image_bytes: bytes) -> np.ndarray:
    """Decode raw image bytes (JPEG/PNG/etc.) into an OpenCV BGR uint8 ndarray."""
    if not image_bytes:
        raise ValueError("image_bytes must not be empty")
    buf = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Failed to decode image bytes")
    return image


def encode_image_bytes(
    image: np.ndarray,
    *,
    fmt: Literal["PNG", "JPEG"] = "PNG",
    quality: int = 92,
) -> bytes:
    """Encode an OpenCV BGR/Gray ndarray into PNG or JPEG bytes."""
    normalized_fmt = fmt.upper()
    if normalized_fmt == "JPEG":
        ext = ".jpg"
        params = [int(cv2.IMWRITE_JPEG_QUALITY), max(1, min(100, int(quality)))]
    elif normalized_fmt == "PNG":
        ext = ".png"
        params = [int(cv2.IMWRITE_PNG_COMPRESSION), 3]
    else:
        raise ValueError(f"Unsupported image format: {fmt}")

    ok, encoded = cv2.imencode(ext, image, params)
    if not ok:
        raise RuntimeError(f"Failed to encode image as {normalized_fmt}")
    return encoded.tobytes()


def _ensure_bgr(image: np.ndarray) -> np.ndarray:
    """Convert grayscale or BGRA images to 3-channel BGR uint8."""
    if image is None or image.size == 0:
        raise ValueError("Input image must not be empty")
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.ndim == 3 and image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    if image.ndim == 3 and image.shape[2] == 3:
        return image
    raise ValueError(f"Unsupported image shape: {image.shape}")


def _normalize_min_area_rect(
    rect: tuple[tuple[float, float], tuple[float, float], float],
) -> tuple[float, float, float, float, float]:
    """Normalize OpenCV `minAreaRect` output so `angle_deg` lies in `(-45.0, 45.0]`.

    Convention (image coordinates with +X right, +Y down):
    - Positive `angle_deg` means the document is tilted clockwise (top edge slopes down
      to the right). Passing `angle_deg` directly to `cv2.getRotationMatrix2D(center,
      angle_deg, 1.0)` rotates the image counter-clockwise by `angle_deg` and levels
      the document horizontally with size `(width, height)`.
    """
    (cx, cy), (rw, rh), raw_angle = rect
    angle = float(raw_angle)
    width = float(rw)
    height = float(rh)

    # Normalize arbitrary angle into (-90, 90] first, then fold into (-45, 45].
    while angle <= -90.0:
        angle += 180.0
    while angle > 90.0:
        angle -= 180.0

    if angle > 45.0:
        angle -= 90.0
        width, height = height, width
    elif angle <= -45.0:
        angle += 90.0
        width, height = height, width

    return float(cx), float(cy), width, height, angle


@dataclass(frozen=True, slots=True)
class CropMarginOffsets:
    """Per-edge margin adjustments for a crop box.

    Positive values expand the crop outward (adding margin); negative values trim
    inward (removing faint borders or edge shadows).
    """

    top: float = 0.0
    right: float = 0.0
    bottom: float = 0.0
    left: float = 0.0
    unit: MarginUnit = "mm"

    @classmethod
    def uniform(cls, value: float, unit: MarginUnit = "mm") -> CropMarginOffsets:
        v = float(value)
        return cls(top=v, right=v, bottom=v, left=v, unit=unit)

    @property
    def is_zero(self) -> bool:
        return (
            abs(self.top) < 1e-9
            and abs(self.right) < 1e-9
            and abs(self.bottom) < 1e-9
            and abs(self.left) < 1e-9
        )

    def to_pixels(self, dpi: float) -> CropMarginOffsets:
        """Convert this margin specification into pixel units."""
        if self.unit == "px":
            return self
        if self.is_zero:
            return CropMarginOffsets(unit="px")
        return CropMarginOffsets(
            top=mm_to_px(self.top, dpi),
            right=mm_to_px(self.right, dpi),
            bottom=mm_to_px(self.bottom, dpi),
            left=mm_to_px(self.left, dpi),
            unit="px",
        )

    def to_millimeters(self, dpi: float) -> CropMarginOffsets:
        """Convert this margin specification into millimeter units."""
        if self.unit == "mm":
            return self
        if self.is_zero:
            return CropMarginOffsets(unit="mm")
        return CropMarginOffsets(
            top=px_to_mm(self.top, dpi),
            right=px_to_mm(self.right, dpi),
            bottom=px_to_mm(self.bottom, dpi),
            left=px_to_mm(self.left, dpi),
            unit="mm",
        )

    def scaled(self, scale_x: float, scale_y: float | None = None) -> CropMarginOffsets:
        """Scale pixel offsets by resolution ratio; physical mm offsets stay unchanged."""
        if self.unit == "mm":
            return self
        sy = scale_x if scale_y is None else scale_y
        return CropMarginOffsets(
            top=self.top * sy,
            right=self.right * scale_x,
            bottom=self.bottom * sy,
            left=self.left * scale_x,
            unit="px",
        )


def _clamp_axis_margins(
    size: float,
    neg_side: float,
    pos_side: float,
    min_size: float,
) -> tuple[float, float, float]:
    """Apply signed margins on one axis while ensuring resulting size >= `min_size`.

    Args:
        size: Base dimension along the axis.
        neg_side: Margin on the negative side (`left` or `top`); positive expands.
        pos_side: Margin on the positive side (`right` or `bottom`); positive expands.
        min_size: Minimum allowable resulting dimension.

    Returns:
        `(new_size, effective_neg_side, effective_pos_side)`
    """
    base = max(float(size), float(min_size))
    n = float(neg_side)
    p = float(pos_side)
    new_size = base + n + p
    if new_size >= min_size:
        return new_size, n, p

    # Clamp negative trims proportionally so the box never collapses or inverts.
    deficit = min_size - new_size
    neg_trim = max(0.0, -n)
    pos_trim = max(0.0, -p)
    total_trim = neg_trim + pos_trim
    if total_trim <= 1e-9:
        return min_size, n, p

    reduce_ratio = min(1.0, deficit / total_trim)
    eff_n = n + neg_trim * reduce_ratio
    eff_p = p + pos_trim * reduce_ratio
    return max(min_size, base + eff_n + eff_p), eff_n, eff_p


@dataclass(frozen=True, slots=True)
class RotatedCropBox:
    """Axis-aligned rectangle rotated by `angle_deg` clockwise in image coordinates.

    Attributes:
        center_x: Center X coordinate in source image pixels.
        center_y: Center Y coordinate in source image pixels.
        width: Unrotated (deskewed) width in source image pixels.
        height: Unrotated (deskewed) height in source image pixels.
        angle_deg: Clockwise tilt angle in degrees. Applying
            `cv2.getRotationMatrix2D((center_x, center_y), angle_deg, 1.0)`
            straightens the rectangle.
        confidence: Detection confidence in `[0.0, 1.0]`.
        index: 1-based reading order index assigned by the detector (0 if unassigned).
        source_width: Width of the image in which coordinates are expressed.
        source_height: Height of the image in which coordinates are expressed.
        margin_mm: Per-edge margin offsets in millimeters (applied via `dpi`).
        margin_px: Per-edge margin offsets in source image pixels.
        manual_angle_offset_deg: User fine-angle adjustment added to `angle_deg`.
        auto_rotation_cw_deg: Automatically detected upright rotation (0/90/180/270).
        manual_rotation_cw_deg: User manual 90°-step rotation override (or None).
        source_dpi: DPI of the image in which this box was detected/adjusted (0 if unknown).
    """

    center_x: float
    center_y: float
    width: float
    height: float
    angle_deg: float
    confidence: float = 1.0
    index: int = 0
    source_width: int = 0
    source_height: int = 0
    margin_mm: CropMarginOffsets = field(default_factory=lambda: CropMarginOffsets(unit="mm"))
    margin_px: CropMarginOffsets = field(default_factory=lambda: CropMarginOffsets(unit="px"))
    manual_angle_offset_deg: float = 0.0
    auto_rotation_cw_deg: RotationDeg = 0
    manual_rotation_cw_deg: RotationDeg | None = None
    source_dpi: float = 0.0

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    @property
    def effective_angle_deg(self) -> float:
        """Total clockwise fine-skew angle combining detected angle and manual tweak."""
        return float(self.angle_deg) + float(self.manual_angle_offset_deg)

    @property
    def effective_rotation_cw_deg(self) -> RotationDeg:
        """Final 90°-step CW rotation to apply (manual override wins over auto)."""
        if self.manual_rotation_cw_deg is not None:
            return normalize_rotation_cw(self.manual_rotation_cw_deg)
        return normalize_rotation_cw(self.auto_rotation_cw_deg)

    def resolved_geometry(
        self,
        dpi: float | None = None,
        *,
        min_size_px: float = MIN_CROP_SIDE_PX,
    ) -> tuple[float, float, float, float, float]:
        """Resolve `(center_x, center_y, width, height, effective_angle_deg)` with margins.

        Applies both `margin_mm` (converted via `dpi` or `self.source_dpi`) and `margin_px`,
        shifting the center along the rotated local axes for asymmetric edge offsets and
        clamping dimensions to at least `min_size_px`.
        """
        eff_dpi = self.source_dpi if dpi is None else float(dpi)
        mm_as_px = (
            self.margin_mm.to_pixels(eff_dpi)
            if (not self.margin_mm.is_zero and eff_dpi > 0.0)
            else CropMarginOffsets(unit="px")
        )
        px_m = (
            self.margin_px
            if self.margin_px.unit == "px"
            else (
                self.margin_px.to_pixels(eff_dpi)
                if eff_dpi > 0.0
                else CropMarginOffsets(unit="px")
            )
        )

        total_top = mm_as_px.top + px_m.top
        total_right = mm_as_px.right + px_m.right
        total_bottom = mm_as_px.bottom + px_m.bottom
        total_left = mm_as_px.left + px_m.left

        eff_w, eff_left, eff_right = _clamp_axis_margins(
            self.width, total_left, total_right, min_size_px
        )
        eff_h, eff_top, eff_bottom = _clamp_axis_margins(
            self.height, total_top, total_bottom, min_size_px
        )

        eff_angle = self.effective_angle_deg
        rad = math.radians(eff_angle)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)

        # Asymmetric edge adjustments shift the center in the box's local frame.
        local_dx = (eff_right - eff_left) * 0.5
        local_dy = (eff_bottom - eff_top) * 0.5
        eff_cx = self.center_x + local_dx * cos_a - local_dy * sin_a
        eff_cy = self.center_y + local_dx * sin_a + local_dy * cos_a
        return float(eff_cx), float(eff_cy), float(eff_w), float(eff_h), float(eff_angle)

    def with_uniform_margin(
        self,
        offset: float,
        *,
        unit: MarginUnit = "mm",
    ) -> RotatedCropBox:
        """Return a copy with uniform margin offset applied to all 4 edges."""
        margins = CropMarginOffsets.uniform(offset, unit=unit)
        if unit == "mm":
            return replace(self, margin_mm=margins)
        return replace(self, margin_px=margins)

    def with_edge_margins(
        self,
        *,
        top: float | None = None,
        right: float | None = None,
        bottom: float | None = None,
        left: float | None = None,
        unit: MarginUnit = "mm",
    ) -> RotatedCropBox:
        """Return a copy updating individual edge margin offsets in mm or px."""
        base = self.margin_mm if unit == "mm" else self.margin_px
        updated = CropMarginOffsets(
            top=base.top if top is None else float(top),
            right=base.right if right is None else float(right),
            bottom=base.bottom if bottom is None else float(bottom),
            left=base.left if left is None else float(left),
            unit=unit,
        )
        if unit == "mm":
            return replace(self, margin_mm=updated)
        return replace(self, margin_px=updated)

    def with_position_offset(self, dx: float, dy: float) -> RotatedCropBox:
        """Return a copy translated by `(dx, dy)` in source image pixels."""
        return replace(
            self,
            center_x=self.center_x + float(dx),
            center_y=self.center_y + float(dy),
        )

    def with_edge_drag(self, edge: EdgeName, delta_px: float) -> RotatedCropBox:
        """Adjust a single edge outward (positive) or inward (negative) by `delta_px`."""
        current = self.margin_px
        delta = float(delta_px)
        updated = CropMarginOffsets(
            top=current.top + (delta if edge == "top" else 0.0),
            right=current.right + (delta if edge == "right" else 0.0),
            bottom=current.bottom + (delta if edge == "bottom" else 0.0),
            left=current.left + (delta if edge == "left" else 0.0),
            unit="px",
        )
        return replace(self, margin_px=updated)

    def with_corner_drag(
        self,
        corner_index: int,
        new_x: float,
        new_y: float,
        *,
        dpi: float | None = None,
        min_size_px: float = 12.0,
    ) -> RotatedCropBox:
        """Resize the rotated crop box by dragging corner `corner_index` (0=TL..3=BL).

        Keeps the opposite corner fixed in image coordinates and bakes any existing
        margin offsets into the updated base rectangle geometry.
        """
        if corner_index not in (0, 1, 2, 3):
            raise ValueError(f"corner_index must be 0..3, got {corner_index}")

        pts = self.corners(dpi=dpi)
        anchor = pts[(corner_index + 2) % 4]
        anchor_x = float(anchor[0])
        anchor_y = float(anchor[1])

        eff_angle = self.effective_angle_deg
        rad = math.radians(eff_angle)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)

        vx = float(new_x) - anchor_x
        vy = float(new_y) - anchor_y
        # Project image-space displacement into the box's rotated local frame.
        local_u = vx * cos_a + vy * sin_a
        local_v = -vx * sin_a + vy * cos_a

        signs = ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))
        sign_u, sign_v = signs[corner_index]

        new_w = max(float(min_size_px), local_u * sign_u)
        new_h = max(float(min_size_px), local_v * sign_v)

        half_u = 0.5 * sign_u * new_w
        half_v = 0.5 * sign_v * new_h
        new_cx = anchor_x + half_u * cos_a - half_v * sin_a
        new_cy = anchor_y + half_u * sin_a + half_v * cos_a

        return replace(
            self,
            center_x=float(new_cx),
            center_y=float(new_cy),
            width=float(new_w),
            height=float(new_h),
            margin_mm=CropMarginOffsets(unit="mm"),
            margin_px=CropMarginOffsets(unit="px"),
        )

    def with_manual_angle_offset(self, delta_deg: float) -> RotatedCropBox:
        """Set manual fine-angle offset in degrees (added to `angle_deg`)."""
        return replace(self, manual_angle_offset_deg=float(delta_deg))

    def with_auto_rotation(self, rotation_cw_deg: int) -> RotatedCropBox:
        """Record the automatically detected 90°-step CW rotation."""
        return replace(self, auto_rotation_cw_deg=normalize_rotation_cw(rotation_cw_deg))

    def with_manual_rotation(self, rotation_cw_deg: int | None) -> RotatedCropBox:
        """Set or clear (`None`) the manual 90°-step CW rotation override."""
        if rotation_cw_deg is None:
            return replace(self, manual_rotation_cw_deg=None)
        return replace(self, manual_rotation_cw_deg=normalize_rotation_cw(rotation_cw_deg))

    def rotate_manual_step(self, delta_cw_deg: int) -> RotatedCropBox:
        """Incrementally rotate the effective 90°-step orientation (e.g. +90 or -90)."""
        next_deg = normalize_rotation_cw(self.effective_rotation_cw_deg + int(delta_cw_deg))
        return replace(self, manual_rotation_cw_deg=next_deg)

    def to_normalized(
        self,
        *,
        source_width: int | None = None,
        source_height: int | None = None,
        source_dpi: float | None = None,
    ) -> NormalizedCropBox:
        """Convert this crop box into resolution-independent normalized coordinates."""
        return NormalizedCropBox.from_crop_box(
            self,
            source_width=source_width,
            source_height=source_height,
            source_dpi=source_dpi,
        )

    def corners(self, dpi: float | None = None) -> np.ndarray:
        """Return the 4 resolved corners as a `(4, 2)` float32 array in TL, TR, BR, BL order."""
        cx, cy, w, h, angle = self.resolved_geometry(dpi=dpi)
        half_w = w * 0.5
        half_h = h * 0.5
        rad = math.radians(angle)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)

        local = (
            (-half_w, -half_h),  # top-left
            (half_w, -half_h),   # top-right
            (half_w, half_h),    # bottom-right
            (-half_w, half_h),   # bottom-left
        )
        pts = [
            (
                cx + dx * cos_a - dy * sin_a,
                cy + dx * sin_a + dy * cos_a,
            )
            for dx, dy in local
        ]
        return np.array(pts, dtype=np.float32)

    def bounding_rect(self, dpi: float | None = None) -> tuple[float, float, float, float]:
        """Return resolved axis-aligned bounding box `(x, y, w, h)` in image coordinates."""
        pts = self.corners(dpi=dpi)
        min_x = float(np.min(pts[:, 0]))
        min_y = float(np.min(pts[:, 1]))
        max_x = float(np.max(pts[:, 0]))
        max_y = float(np.max(pts[:, 1]))
        return min_x, min_y, max_x - min_x, max_y - min_y


@dataclass(frozen=True, slots=True)
class NormalizedCropBox:
    """Resolution-independent crop box representation for preview-to-main-scan mapping.

    Geometry is normalized by source image dimensions (`[0.0, 1.0]` range), physical
    margin offsets are preserved in millimeters, pixel margin offsets are preserved as
    normalized fractions of source width/height, and both fine-angle tweaks and 90°-step
    rotation settings carry over unchanged.
    """

    center_x_norm: float
    center_y_norm: float
    width_norm: float
    height_norm: float
    angle_deg: float
    confidence: float = 1.0
    index: int = 0
    margin_mm: CropMarginOffsets = field(default_factory=lambda: CropMarginOffsets(unit="mm"))
    margin_top_norm: float = 0.0
    margin_right_norm: float = 0.0
    margin_bottom_norm: float = 0.0
    margin_left_norm: float = 0.0
    manual_angle_offset_deg: float = 0.0
    auto_rotation_cw_deg: RotationDeg = 0
    manual_rotation_cw_deg: RotationDeg | None = None

    @classmethod
    def from_crop_box(
        cls,
        box: RotatedCropBox,
        *,
        source_width: int | None = None,
        source_height: int | None = None,
        source_dpi: float | None = None,
    ) -> NormalizedCropBox:
        src_w = box.source_width if source_width is None else int(source_width)
        src_h = box.source_height if source_height is None else int(source_height)
        if src_w <= 0 or src_h <= 0:
            raise ValueError(
                f"Valid positive source dimensions are required, got ({src_w}, {src_h})"
            )

        # Preserve physical mm margins directly; express pixel margins as fractions of
        # source dimensions so they scale accurately even if DPI metadata is omitted.
        mm_margins = box.margin_mm
        px_margins = box.margin_px
        eff_dpi = box.source_dpi if source_dpi is None else float(source_dpi)
        if px_margins.unit == "mm":
            px_margins = (
                px_margins.to_pixels(eff_dpi)
                if eff_dpi > 0.0
                else CropMarginOffsets(unit="px")
            )

        return cls(
            center_x_norm=box.center_x / float(src_w),
            center_y_norm=box.center_y / float(src_h),
            width_norm=box.width / float(src_w),
            height_norm=box.height / float(src_h),
            angle_deg=box.angle_deg,
            confidence=box.confidence,
            index=box.index,
            margin_mm=mm_margins,
            margin_top_norm=px_margins.top / float(src_h),
            margin_right_norm=px_margins.right / float(src_w),
            margin_bottom_norm=px_margins.bottom / float(src_h),
            margin_left_norm=px_margins.left / float(src_w),
            manual_angle_offset_deg=box.manual_angle_offset_deg,
            auto_rotation_cw_deg=box.auto_rotation_cw_deg,
            manual_rotation_cw_deg=box.manual_rotation_cw_deg,
        )

    def to_crop_box(
        self,
        target_width: int,
        target_height: int,
        *,
        target_dpi: float = 0.0,
    ) -> RotatedCropBox:
        """Project this normalized crop box onto a target scan image resolution."""
        if target_width <= 0 or target_height <= 0:
            raise ValueError(
                f"Target dimensions must be positive, got ({target_width}, {target_height})"
            )

        tw = float(target_width)
        th = float(target_height)
        scaled_px_margins = CropMarginOffsets(
            top=self.margin_top_norm * th,
            right=self.margin_right_norm * tw,
            bottom=self.margin_bottom_norm * th,
            left=self.margin_left_norm * tw,
            unit="px",
        )
        return RotatedCropBox(
            center_x=self.center_x_norm * tw,
            center_y=self.center_y_norm * th,
            width=self.width_norm * tw,
            height=self.height_norm * th,
            angle_deg=self.angle_deg,
            confidence=self.confidence,
            index=self.index,
            source_width=int(target_width),
            source_height=int(target_height),
            margin_mm=self.margin_mm,
            margin_px=scaled_px_margins,
            manual_angle_offset_deg=self.manual_angle_offset_deg,
            auto_rotation_cw_deg=self.auto_rotation_cw_deg,
            manual_rotation_cw_deg=self.manual_rotation_cw_deg,
            source_dpi=float(target_dpi),
        )


def scale_crop_box(
    box: RotatedCropBox,
    *,
    target_width: int,
    target_height: int,
    target_dpi: float = 0.0,
    source_width: int | None = None,
    source_height: int | None = None,
) -> RotatedCropBox:
    """Scale a preview-space `RotatedCropBox` to high-resolution main-scan pixel coordinates."""
    normalized = NormalizedCropBox.from_crop_box(
        box,
        source_width=source_width,
        source_height=source_height,
    )
    return normalized.to_crop_box(
        target_width=target_width,
        target_height=target_height,
        target_dpi=target_dpi,
    )


def scale_crop_boxes(
    boxes: Sequence[RotatedCropBox],
    *,
    target_width: int,
    target_height: int,
    target_dpi: float = 0.0,
    source_width: int | None = None,
    source_height: int | None = None,
) -> list[RotatedCropBox]:
    """Scale a sequence of `RotatedCropBox` instances to target scan resolution."""
    return [
        scale_crop_box(
            box,
            target_width=target_width,
            target_height=target_height,
            target_dpi=target_dpi,
            source_width=source_width,
            source_height=source_height,
        )
        for box in boxes
    ]


@dataclass(frozen=True, slots=True)
class DetectionConfig:
    """Parameters controlling multi-document region detection."""

    background_mode: BackgroundMode = "auto"
    multi_document: bool = True
    min_area_ratio: float = 0.004
    max_area_ratio: float = 0.985
    min_side_px: int = 24
    min_rectangularity: float = 0.45
    detection_max_dimension: int = 1600
    color_diff_min_threshold: float = 10.0
    color_diff_mad_scale: float = 4.5
    use_edge_assist: bool = True
    overlap_containment_threshold: float = 0.65


@dataclass(frozen=True, slots=True)
class DeskewConfig:
    """Parameters controlling fine deskew angle refinement and subpixel cropping."""

    refine_angle: bool = True
    max_residual_angle_deg: float = 5.0
    coarse_step_deg: float = 0.5
    fine_step_deg: float = 0.1
    fine_window_deg: float = 0.8
    hough_angle_tolerance_deg: float = 7.0
    padding_ratio: float = 0.08
    interpolation: int = cv2.INTER_CUBIC


@dataclass(frozen=True, slots=True)
class CroppedDocument:
    """A single deskewed and cropped document extracted from a scanned image."""

    box: RotatedCropBox
    image: np.ndarray
    raw_angle_deg: float
    refined_angle_deg: float
    residual_angle_deg: float
    angle_method: str


class MultiDocumentDetector:
    """Detects one or more rotated paper documents against a scanner platen background."""

    def __init__(self, config: DetectionConfig | None = None) -> None:
        self.config = config or DetectionConfig()

    def detect(
        self,
        image: np.ndarray | bytes,
        *,
        dpi: float = 0.0,
    ) -> list[RotatedCropBox]:
        """Detect rotated document bounding boxes in `image`.

        Args:
            image: OpenCV BGR/Gray ndarray or raw encoded image bytes.
            dpi: Optional source scan DPI stored on each returned `RotatedCropBox`.

        Returns:
            List of `RotatedCropBox` sorted in reading order (top-to-bottom, left-to-right),
            with 1-based `index` values.
        """
        bgr = _ensure_bgr(decode_image_bytes(image) if isinstance(image, bytes) else image)
        orig_h, orig_w = bgr.shape[:2]

        scale = 1.0
        max_dim = max(orig_h, orig_w)
        max_detect = self.config.detection_max_dimension
        if max_detect > 0 and max_dim > max_detect:
            scale = max_detect / float(max_dim)
            work_w = max(1, int(round(orig_w * scale)))
            work_h = max(1, int(round(orig_h * scale)))
            work_bgr = cv2.resize(bgr, (work_w, work_h), interpolation=cv2.INTER_AREA)
        else:
            work_bgr = bgr

        work_h, work_w = work_bgr.shape[:2]
        work_area = float(work_h * work_w)

        mask = self._build_foreground_mask(work_bgr)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        inv_scale = 1.0 / scale
        min_side_work = max(8.0, self.config.min_side_px * scale)
        candidates: list[RotatedCropBox] = []

        for contour in contours:
            contour_area = float(cv2.contourArea(contour))
            if contour_area <= 0.0:
                continue

            rect = cv2.minAreaRect(contour)
            cx, cy, rw, rh, angle_deg = _normalize_min_area_rect(rect)
            rect_area = rw * rh
            if rect_area <= 0.0:
                continue

            area_ratio = rect_area / work_area
            if area_ratio < self.config.min_area_ratio:
                continue
            if area_ratio > self.config.max_area_ratio:
                continue
            if min(rw, rh) < min_side_work:
                continue

            rectangularity = min(1.0, contour_area / rect_area)
            if rectangularity < self.config.min_rectangularity:
                continue

            candidates.append(
                RotatedCropBox(
                    center_x=cx * inv_scale,
                    center_y=cy * inv_scale,
                    width=rw * inv_scale,
                    height=rh * inv_scale,
                    angle_deg=angle_deg,
                    confidence=round(float(rectangularity), 4),
                    index=0,
                    source_width=orig_w,
                    source_height=orig_h,
                    source_dpi=float(dpi),
                )
            )

        filtered = self._suppress_contained_boxes(candidates)
        if not filtered:
            return []

        if not self.config.multi_document:
            largest = max(filtered, key=lambda b: b.area)
            return [replace(largest, index=1)]

        sorted_boxes = self._sort_reading_order(filtered)
        return [replace(box, index=i) for i, box in enumerate(sorted_boxes, start=1)]

    def _estimate_background_stats(
        self, lab: np.ndarray
    ) -> tuple[np.ndarray, float]:
        """Estimate background Lab color and robust noise level (MAD) from image borders."""
        h, w = lab.shape[:2]
        band = max(4, min(20, int(round(min(h, w) * 0.015))))

        top = lab[:band, band : max(band + 1, w - band)].reshape(-1, 3)
        bottom = lab[max(0, h - band) :, band : max(band + 1, w - band)].reshape(-1, 3)
        left = lab[band : max(band + 1, h - band), :band].reshape(-1, 3)
        right = lab[band : max(band + 1, h - band), max(0, w - band) :].reshape(-1, 3)

        border_pixels = np.concatenate([top, bottom, left, right], axis=0).astype(np.float32)
        if border_pixels.size == 0:
            border_pixels = lab.reshape(-1, 3).astype(np.float32)

        if self.config.background_mode == "light":
            l_vals = border_pixels[:, 0]
            cutoff = np.percentile(l_vals, 50)
            selected = border_pixels[l_vals >= cutoff]
            if len(selected) > 0:
                border_pixels = selected
        elif self.config.background_mode == "dark":
            l_vals = border_pixels[:, 0]
            cutoff = np.percentile(l_vals, 50)
            selected = border_pixels[l_vals <= cutoff]
            if len(selected) > 0:
                border_pixels = selected

        bg_lab = np.median(border_pixels, axis=0)
        dists = np.linalg.norm(border_pixels - bg_lab, axis=1)
        mad = float(np.median(np.abs(dists - np.median(dists))))
        # 1.4826 converts MAD to Gaussian-equivalent sigma.
        sigma = mad * 1.4826
        return bg_lab, sigma

    def _build_foreground_mask(self, bgr: np.ndarray) -> np.ndarray:
        """Build a clean binary mask separating documents from the platen background."""
        h, w = bgr.shape[:2]
        min_dim = min(h, w)
        blur_k = max(3, int(round(min_dim * 0.004)) | 1)
        blur_k = min(blur_k, 9)

        blurred = cv2.GaussianBlur(bgr, (blur_k, blur_k), 0)
        lab = cv2.cvtColor(blurred, cv2.COLOR_BGR2LAB)
        bg_lab, bg_sigma = self._estimate_background_stats(lab)

        diff_lab = lab.astype(np.float32) - bg_lab.reshape(1, 1, 3)
        # Weight L channel slightly stronger for subtle paper-vs-platen brightness differences.
        delta_e = np.sqrt(
            (diff_lab[:, :, 0] * 1.15) ** 2
            + diff_lab[:, :, 1] ** 2
            + diff_lab[:, :, 2] ** 2
        )

        threshold = max(
            self.config.color_diff_min_threshold,
            bg_sigma * self.config.color_diff_mad_scale + 4.0,
        )
        color_mask = (delta_e >= threshold).astype(np.uint8) * 255

        # Clean up small speckles without merging nearby documents.
        open_k = max(3, int(round(min_dim * 0.004)) | 1)
        open_k = min(open_k, 7)
        close_k = max(5, int(round(min_dim * 0.008)) | 1)
        close_k = min(close_k, 15)

        open_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_k, open_k))
        close_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_k, close_k))

        color_mask = cv2.morphologyEx(color_mask, cv2.MORPH_OPEN, open_kernel)
        color_mask = cv2.morphologyEx(color_mask, cv2.MORPH_CLOSE, close_kernel)

        if not self.config.use_edge_assist:
            return self._fill_external_contours(color_mask)

        color_filled = self._fill_external_contours(color_mask)

        # Edge-assisted mask catches white paper on white platen via perimeter shadow edges.
        # Use Canny (1px non-maximum suppressed edges) so boundaries do not bloat outward,
        # and only add edge-derived components that were NOT already detected by color_mask.
        gray = cv2.cvtColor(blurred, cv2.COLOR_BGR2GRAY)
        grad_x = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        grad_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        grad_mag = cv2.magnitude(grad_x, grad_y)

        band = max(4, min(20, int(round(min_dim * 0.015))))
        border_grad = np.concatenate(
            [
                grad_mag[:band, :].ravel(),
                grad_mag[-band:, :].ravel(),
                grad_mag[:, :band].ravel(),
                grad_mag[:, -band:].ravel(),
            ]
        )
        grad_high = max(22.0, float(np.percentile(border_grad, 98)) * 2.4 + 10.0)
        grad_low = max(10.0, grad_high * 0.45)
        edge_mask = cv2.Canny(
            gray,
            int(round(grad_low)),
            int(round(grad_high)),
            apertureSize=3,
        )

        # Suppress 2px outer frame border artifacts.
        edge_mask[:2, :] = 0
        edge_mask[-2:, :] = 0
        edge_mask[:, :2] = 0
        edge_mask[:, -2:] = 0

        edge_close_k = max(5, int(round(min_dim * 0.01)) | 1)
        edge_close_k = min(edge_close_k, 13)
        edge_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (edge_close_k, edge_close_k)
        )
        edge_closed = cv2.morphologyEx(edge_mask, cv2.MORPH_CLOSE, edge_kernel)
        edge_filled = self._fill_external_contours(edge_closed)

        # Add only edge-filled components that are not already covered by color_filled.
        edge_contours, _ = cv2.findContours(
            edge_filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        combined = color_filled.copy()
        for cnt in edge_contours:
            area = float(cv2.contourArea(cnt))
            if area <= 0.0:
                continue
            comp_mask = np.zeros_like(color_filled)
            cv2.drawContours(comp_mask, [cnt], -1, 255, thickness=cv2.FILLED)
            overlap = float(np.count_nonzero(cv2.bitwise_and(comp_mask, color_filled)))
            if overlap / area < 0.35:
                combined = cv2.bitwise_or(combined, comp_mask)

        combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, open_kernel)
        return self._fill_external_contours(combined)

    @staticmethod
    def _fill_external_contours(binary_mask: np.ndarray) -> np.ndarray:
        """Fill interiors of external contours so printed text/holes stay part of the page."""
        contours, _ = cv2.findContours(
            binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        filled = np.zeros_like(binary_mask)
        if contours:
            cv2.drawContours(filled, contours, -1, 255, thickness=cv2.FILLED)
        return filled

    def _suppress_contained_boxes(
        self, boxes: list[RotatedCropBox]
    ) -> list[RotatedCropBox]:
        """Remove boxes that are mostly contained inside a larger detected box."""
        if len(boxes) <= 1:
            return boxes

        ordered = sorted(boxes, key=lambda b: b.area, reverse=True)
        kept: list[RotatedCropBox] = []
        threshold = self.config.overlap_containment_threshold

        for cand in ordered:
            cand_poly = cand.corners()
            is_contained = False
            for existing in kept:
                exist_poly = existing.corners()
                inter_area, _ = cv2.intersectConvexConvex(cand_poly, exist_poly)
                if cand.area > 0 and (inter_area / cand.area) >= threshold:
                    is_contained = True
                    break
            if not is_contained:
                kept.append(cand)
        return kept

    @staticmethod
    def _sort_reading_order(boxes: list[RotatedCropBox]) -> list[RotatedCropBox]:
        """Sort detected documents roughly top-to-bottom in rows, then left-to-right."""
        if len(boxes) <= 1:
            return boxes

        # Cluster boxes into visual rows based on vertical overlap of centers.
        by_y = sorted(boxes, key=lambda b: b.center_y)
        rows: list[list[RotatedCropBox]] = []
        for box in by_y:
            placed = False
            for row in rows:
                avg_y = sum(b.center_y for b in row) / len(row)
                avg_h = sum(b.height for b in row) / len(row)
                if abs(box.center_y - avg_y) <= avg_h * 0.45:
                    row.append(box)
                    placed = True
                    break
            if not placed:
                rows.append([box])

        ordered: list[RotatedCropBox] = []
        for row in rows:
            row.sort(key=lambda b: b.center_x)
            ordered.extend(row)
        return ordered


class DeskewCropProcessor:
    """Refines fine tilt angles to 0.1-degree precision and extracts deskewed crops."""

    def __init__(
        self,
        detector: MultiDocumentDetector | None = None,
        config: DeskewConfig | None = None,
    ) -> None:
        self.detector = detector or MultiDocumentDetector()
        self.config = config or DeskewConfig()

    def process_image(
        self,
        image: np.ndarray | bytes,
        *,
        boxes: list[RotatedCropBox] | None = None,
        dpi: float | None = None,
    ) -> list[CroppedDocument]:
        """Detect (if `boxes` is omitted), refine skew, and crop all documents."""
        bgr = _ensure_bgr(decode_image_bytes(image) if isinstance(image, bytes) else image)
        detect_dpi = float(dpi) if dpi is not None else 0.0
        target_boxes = boxes if boxes is not None else self.detector.detect(bgr, dpi=detect_dpi)
        return [self.crop_box(bgr, box, dpi=dpi) for box in target_boxes]

    def crop_box(
        self,
        image: np.ndarray | bytes,
        box: RotatedCropBox,
        *,
        refine_angle: bool | None = None,
        dpi: float | None = None,
    ) -> CroppedDocument:
        """Refine a single box's tilt angle (optional) and extract the straightened crop."""
        bgr = _ensure_bgr(decode_image_bytes(image) if isinstance(image, bytes) else image)
        has_manual_angle = abs(box.manual_angle_offset_deg) > 1e-6
        if refine_angle is None:
            do_refine = self.config.refine_angle and not has_manual_angle
        else:
            do_refine = refine_angle

        eff_cx, eff_cy, eff_w, eff_h, eff_angle = box.resolved_geometry(dpi=dpi)
        raw_angle = eff_angle
        residual = 0.0
        method = "manual" if has_manual_angle and not do_refine else "coarse"

        if do_refine:
            padded_w = max(16.0, eff_w * (1.0 + 2.0 * self.config.padding_ratio))
            padded_h = max(16.0, eff_h * (1.0 + 2.0 * self.config.padding_ratio))
            coarse_patch = self._warp_crop(
                bgr,
                center=(eff_cx, eff_cy),
                width=padded_w,
                height=padded_h,
                angle_deg=raw_angle,
                interpolation=cv2.INTER_LINEAR,
            )
            residual, method = self.estimate_residual_angle(
                coarse_patch,
                inner_width=eff_w,
                inner_height=eff_h,
            )

        refined_angle = raw_angle + residual
        cropped = self._warp_crop(
            bgr,
            center=(eff_cx, eff_cy),
            width=eff_w,
            height=eff_h,
            angle_deg=refined_angle,
            interpolation=self.config.interpolation,
        )

        # Keep manual_angle_offset_deg intact while folding auto-estimated residual into angle_deg.
        refined_box = replace(box, angle_deg=box.angle_deg + residual)
        return CroppedDocument(
            box=refined_box,
            image=cropped,
            raw_angle_deg=raw_angle,
            refined_angle_deg=refined_angle,
            residual_angle_deg=residual,
            angle_method=method,
        )

    def estimate_residual_angle(
        self,
        patch: np.ndarray,
        *,
        inner_width: float | None = None,
        inner_height: float | None = None,
    ) -> tuple[float, str]:
        """Estimate fine residual skew angle (in degrees, 0.1° resolution) of a patch.

        Combines:
        1. Hough line segment orientation on document perimeter edges and internal rules.
        2. Projection profile sharpness maximization across horizontal & vertical axes
           on the interior region (effective for printed text lines).

        Returns:
            `(residual_angle_deg, method_name)` where adding `residual_angle_deg` to the
            coarse clockwise tilt angle levels the patch.
        """
        bgr = _ensure_bgr(patch)
        h, w = bgr.shape[:2]
        if min(h, w) < 24:
            return 0.0, "coarse"

        # Downsample very large patches to keep refinement fast and consistent.
        max_side = max(h, w)
        if max_side > 1000:
            scale = 1000.0 / float(max_side)
            work = cv2.resize(
                bgr,
                (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
                interpolation=cv2.INTER_AREA,
            )
            scaled_inner_w = inner_width * scale if inner_width is not None else None
            scaled_inner_h = inner_height * scale if inner_height is not None else None
        else:
            work = bgr
            scaled_inner_w = inner_width
            scaled_inner_h = inner_height

        gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)

        hough_angle, hough_conf = self._estimate_angle_hough(gray)
        proj_angle, proj_conf = self._estimate_angle_projection(
            gray,
            inner_width=scaled_inner_w,
            inner_height=scaled_inner_h,
        )

        max_res = self.config.max_residual_angle_deg

        # Prefer projection when internal text structure has strong periodic signal,
        # or blend with Hough when both agree closely.
        if hough_conf >= 0.5 and proj_conf >= 0.35:
            if abs(hough_angle - proj_angle) <= 1.2:
                weight_h = hough_conf
                weight_p = proj_conf * 1.15
                blended = (hough_angle * weight_h + proj_angle * weight_p) / (
                    weight_h + weight_p
                )
                return float(np.clip(blended, -max_res, max_res)), "hybrid"
            if proj_conf > hough_conf * 1.15:
                return float(np.clip(proj_angle, -max_res, max_res)), "projection"
            return float(np.clip(hough_angle, -max_res, max_res)), "hough"

        if hough_conf >= 0.35:
            return float(np.clip(hough_angle, -max_res, max_res)), "hough"
        if proj_conf >= 0.25:
            return float(np.clip(proj_angle, -max_res, max_res)), "projection"
        return 0.0, "coarse"

    def _estimate_angle_hough(self, gray: np.ndarray) -> tuple[float, float]:
        """Estimate fine residual angle from near-horizontal/near-vertical Hough lines."""
        h, w = gray.shape[:2]
        min_dim = min(h, w)
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)

        med = float(np.median(blurred))
        low = max(15, int(0.45 * med))
        high = max(45, int(1.15 * med))
        edges = cv2.Canny(blurred, low, high, apertureSize=3)

        min_line_len = max(20, int(round(min_dim * 0.14)))
        max_gap = max(5, int(round(min_dim * 0.025)))
        lines = cv2.HoughLinesP(
            edges,
            rho=1.0,
            theta=math.pi / 1800.0,  # 0.1-degree angular resolution
            threshold=max(25, int(round(min_dim * 0.08))),
            minLineLength=min_line_len,
            maxLineGap=max_gap,
        )
        if lines is None or len(lines) == 0:
            return 0.0, 0.0

        tol = self.config.hough_angle_tolerance_deg
        angles: list[float] = []
        weights: list[float] = []

        for line in lines.reshape(-1, 4):
            x1, y1, x2, y2 = (float(line[0]), float(line[1]), float(line[2]), float(line[3]))
            dx = x2 - x1
            dy = y2 - y1
            length = math.hypot(dx, dy)
            if length < min_line_len:
                continue

            # Angle in (-90, 90] where positive means clockwise tilt from horizontal.
            raw_deg = math.degrees(math.atan2(dy, dx))
            if raw_deg > 90.0:
                raw_deg -= 180.0
            elif raw_deg <= -90.0:
                raw_deg += 180.0

            # Fold near-vertical lines (±90°) to residual angle around 0°.
            if abs(raw_deg) <= tol:
                residual = raw_deg
            elif abs(abs(raw_deg) - 90.0) <= tol:
                residual = raw_deg - 90.0 if raw_deg > 0.0 else raw_deg + 90.0
            else:
                continue

            if abs(residual) <= self.config.max_residual_angle_deg:
                angles.append(residual)
                weights.append(length)

        if not angles:
            return 0.0, 0.0

        ang_arr = np.array(angles, dtype=np.float64)
        w_arr = np.array(weights, dtype=np.float64)
        total_w = float(np.sum(w_arr))
        if total_w <= 0.0:
            return 0.0, 0.0

        # Compute weighted median for outlier robustness.
        order = np.argsort(ang_arr)
        ang_sorted = ang_arr[order]
        w_sorted = w_arr[order]
        cum_w = np.cumsum(w_sorted)
        med_idx = int(np.searchsorted(cum_w, total_w * 0.5))
        med_idx = min(med_idx, len(ang_sorted) - 1)
        weighted_median = float(ang_sorted[med_idx])

        # Refine with weighted mean of inliers within 0.6 degrees of the median.
        inliers = np.abs(ang_arr - weighted_median) <= 0.6
        if np.any(inliers):
            refined = float(np.average(ang_arr[inliers], weights=w_arr[inliers]))
            inlier_ratio = float(np.sum(w_arr[inliers])) / total_w
        else:
            refined = weighted_median
            inlier_ratio = 0.5

        spread = float(np.average(np.abs(ang_arr - refined), weights=w_arr))
        length_score = min(1.0, total_w / float(max(h, w) * 2.0))
        consistency_score = max(0.0, 1.0 - spread / 2.5)
        confidence = float(length_score * (0.4 + 0.6 * consistency_score) * inlier_ratio)
        return refined, confidence

    def _estimate_angle_projection(
        self,
        gray: np.ndarray,
        *,
        inner_width: float | None = None,
        inner_height: float | None = None,
    ) -> tuple[float, float]:
        """Estimate fine residual angle via projection-profile variance maximization."""
        h, w = gray.shape[:2]

        # Focus on interior 84% of the document to ignore outer platen border edges.
        if inner_width is not None and inner_height is not None:
            roi_w = max(20, int(round(min(w, inner_width) * 0.84)))
            roi_h = max(20, int(round(min(h, inner_height) * 0.84)))
        else:
            roi_w = max(20, int(round(w * 0.78)))
            roi_h = max(20, int(round(h * 0.78)))

        x0 = max(0, (w - roi_w) // 2)
        y0 = max(0, (h - roi_h) // 2)
        roi = gray[y0 : y0 + roi_h, x0 : x0 + roi_w]
        if roi.size == 0 or min(roi.shape[:2]) < 20:
            return 0.0, 0.0

        # Extract high-contrast foreground features (text strokes, rules).
        block_size = max(11, (min(roi.shape[:2]) // 16) | 1)
        block_size = min(block_size, 41)
        binary = cv2.adaptiveThreshold(
            roi,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            block_size,
            12,
        )

        # Require enough ink/feature pixels to avoid chasing noise on blank paper.
        ink_ratio = float(np.count_nonzero(binary)) / float(binary.size)
        if ink_ratio < 0.005 or ink_ratio > 0.65:
            return 0.0, 0.0

        # Downsample binary image to <= 500px for rapid multi-angle scoring.
        bh, bw = binary.shape[:2]
        if max(bh, bw) > 500:
            scale = 500.0 / float(max(bh, bw))
            binary = cv2.resize(
                binary,
                (max(1, int(round(bw * scale))), max(1, int(round(bh * scale)))),
                interpolation=cv2.INTER_AREA,
            )

        max_res = self.config.max_residual_angle_deg
        coarse_angles = np.arange(
            -max_res,
            max_res + self.config.coarse_step_deg * 0.5,
            self.config.coarse_step_deg,
            dtype=np.float64,
        )

        coarse_scores = np.array(
            [self._projection_sharpness(binary, float(a)) for a in coarse_angles],
            dtype=np.float64,
        )
        best_coarse_idx = int(np.argmax(coarse_scores))
        best_coarse = float(coarse_angles[best_coarse_idx])

        # Fine search around best coarse angle at 0.1-degree step.
        fine_min = max(-max_res, best_coarse - self.config.fine_window_deg)
        fine_max = min(max_res, best_coarse + self.config.fine_window_deg)
        fine_angles = np.arange(
            fine_min,
            fine_max + self.config.fine_step_deg * 0.5,
            self.config.fine_step_deg,
            dtype=np.float64,
        )
        fine_scores = np.array(
            [self._projection_sharpness(binary, float(a)) for a in fine_angles],
            dtype=np.float64,
        )
        best_fine_idx = int(np.argmax(fine_scores))
        best_angle = float(fine_angles[best_fine_idx])
        best_score = float(fine_scores[best_fine_idx])

        median_score = float(np.median(coarse_scores))
        if median_score <= 0.0:
            return 0.0, 0.0

        # Peak-to-median contrast ratio measures how crisp the line alignment peak is.
        peak_ratio = (best_score - median_score) / median_score
        confidence = float(min(1.0, max(0.0, peak_ratio / 1.5)))
        return best_angle, confidence

    @staticmethod
    def _projection_sharpness(binary: np.ndarray, angle_deg: float) -> float:
        """Compute alignment sharpness across horizontal and vertical projections."""
        h, w = binary.shape[:2]
        if abs(angle_deg) > 1e-6:
            mat = cv2.getRotationMatrix2D((w * 0.5, h * 0.5), angle_deg, 1.0)
            rotated = cv2.warpAffine(
                binary,
                mat,
                (w, h),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=0,
            )
        else:
            rotated = binary

        # Normalize to [0, 1] float for stable projection metrics.
        norm = rotated.astype(np.float32) * (1.0 / 255.0)
        h_proj = np.mean(norm, axis=1)
        v_proj = np.mean(norm, axis=0)

        # Combine profile variance and first-difference energy (sharp transitions between
        # text rows/columns and whitespace).
        h_score = float(np.var(h_proj)) + float(np.mean(np.diff(h_proj) ** 2)) * 2.0
        v_score = float(np.var(v_proj)) + float(np.mean(np.diff(v_proj) ** 2)) * 2.0
        return max(h_score, v_score)

    @staticmethod
    def _warp_crop(
        image: np.ndarray,
        *,
        center: tuple[float, float],
        width: float,
        height: float,
        angle_deg: float,
        interpolation: int = cv2.INTER_CUBIC,
    ) -> np.ndarray:
        """Rotate `image` around `center` by `angle_deg` and crop `(width, height)` in one pass."""
        out_w = max(1, int(round(width)))
        out_h = max(1, int(round(height)))

        rot_mat = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
        # Shift rotation center to the center of the output crop canvas.
        rot_mat[0, 2] += (out_w * 0.5) - center[0]
        rot_mat[1, 2] += (out_h * 0.5) - center[1]

        return cv2.warpAffine(
            image,
            rot_mat,
            (out_w, out_h),
            flags=interpolation,
            borderMode=cv2.BORDER_REPLICATE,
        )
