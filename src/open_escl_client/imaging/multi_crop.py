"""Multi-document detection and high-precision fine deskew / cropping engine.

Detects one or more arbitrarily rotated paper documents placed on a scanner platen
(light or dark background), refines each document's fine skew angle using edge lines
and internal text/structure alignment, and extracts straight rectangular crops using
single-pass subpixel affine warping.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

import cv2
import numpy as np

BackgroundMode = Literal["auto", "light", "dark"]
DetectionPreset = Literal["auto", "receipt_multi", "standard_sheet", "full_page"]
ResolvedDetectionPreset = Literal["receipt_multi", "standard_sheet", "full_page"]
RotationDeg = Literal[0, 90, 180, 270]
MarginUnit = Literal["mm", "px"]
EdgeName = Literal["top", "right", "bottom", "left"]

VALID_DETECTION_PRESETS: tuple[DetectionPreset, ...] = (
    "auto",
    "receipt_multi",
    "standard_sheet",
    "full_page",
)

MM_PER_INCH = 25.4
MIN_CROP_SIDE_PX = 4.0

# Standard physical paper dimensions (short_mm, long_mm, name) used to complete
# unanchored edges when only 1-2 sides of a document cast crisp scanner shadows.
STANDARD_PAPER_SPECS_MM: tuple[tuple[float, float, str], ...] = (
    (100.0, 148.0, "postcard"),
    (102.0, 152.0, "kg_photo"),
    (105.0, 148.0, "a6"),
    (89.0, 127.0, "l_photo"),
    (127.0, 178.0, "2l_photo"),
    (128.0, 182.0, "b6"),
    (148.0, 210.0, "a5"),
    (182.0, 257.0, "b5"),
    (210.0, 297.0, "a4"),
    (55.0, 91.0, "business_card"),
)


def normalize_detection_preset(preset: str) -> DetectionPreset:
    """Normalize a detection preset string into a valid `DetectionPreset` literal."""
    cleaned = str(preset).strip().lower()
    if cleaned == "receipt_multi":
        return "receipt_multi"
    if cleaned == "standard_sheet":
        return "standard_sheet"
    if cleaned == "full_page":
        return "full_page"
    return "auto"


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
    detection_preset: DetectionPreset = "auto"
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
    full_page_paper_fallback: bool = True
    paper_color_match_max_delta_e: float = 9.0
    printed_border_ink_drop_l: float = 40.0
    merge_same_sheet_fragments: bool = True
    aspect_ratio_completion: bool = True
    detection_sensitivity: int = 0


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
        self._last_resolved_preset: ResolvedDetectionPreset = "receipt_multi"

    @property
    def last_resolved_preset(self) -> ResolvedDetectionPreset:
        """Return the concrete detection preset selected by the most recent `detect()` call."""
        return self._last_resolved_preset

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
        preset = normalize_detection_preset(self.config.detection_preset)

        if preset == "full_page":
            self._last_resolved_preset = "full_page"
            return [
                RotatedCropBox(
                    center_x=orig_w * 0.5,
                    center_y=orig_h * 0.5,
                    width=float(orig_w),
                    height=float(orig_h),
                    angle_deg=0.0,
                    confidence=1.0,
                    index=1,
                    source_width=orig_w,
                    source_height=orig_h,
                    source_dpi=float(dpi),
                )
            ]

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
        work_lab = cv2.cvtColor(work_bgr, cv2.COLOR_BGR2LAB)
        bg_lab, _ = self._estimate_background_stats(work_lab)
        bg_l = float(bg_lab[0])
        bg_chroma = math.hypot(float(bg_lab[1]) - 128.0, float(bg_lab[2]) - 128.0)
        is_light_platen = bg_l >= 185.0 and bg_chroma <= 24.0

        solid_candidates: list[RotatedCropBox] = []
        printed_fragments: list[tuple[RotatedCropBox, np.ndarray]] = []
        frag_min_area_ratio = min(self.config.min_area_ratio, 0.00008)
        border_guard = max(4.0, min(10.0, min(work_w, work_h) * 0.006))

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
            if area_ratio > self.config.max_area_ratio:
                continue

            rectangularity = min(1.0, contour_area / rect_area)
            is_regular = (
                area_ratio >= self.config.min_area_ratio
                and min(rw, rh) >= min_side_work
                and rectangularity >= self.config.min_rectangularity
            )
            inside_border_guard = (
                border_guard <= cx <= (work_w - border_guard)
                and border_guard <= cy <= (work_h - border_guard)
            )
            is_small_fragment = (
                is_light_platen
                and self.config.merge_same_sheet_fragments
                and inside_border_guard
                and area_ratio >= frag_min_area_ratio
                and min(rw, rh) >= 5.0
                and max(rw, rh) >= 9.0
                and rectangularity >= 0.25
            )
            if not is_regular and not is_small_fragment:
                continue

            work_box = RotatedCropBox(
                center_x=cx,
                center_y=cy,
                width=rw,
                height=rh,
                angle_deg=angle_deg,
                confidence=round(float(rectangularity), 4),
                index=0,
                source_width=work_w,
                source_height=work_h,
                source_dpi=float(dpi),
            )

            if (
                is_light_platen
                and self.config.merge_same_sheet_fragments
                and self._is_white_sheet_printed_fragment(work_lab, bg_lab, work_box)
            ):
                printed_fragments.append((work_box, contour))
            elif is_regular:
                solid_candidates.append(
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

        merged_printed: list[RotatedCropBox] = []
        merged_printed_raw: list[RotatedCropBox] = []
        merged_pairs: list[tuple[RotatedCropBox, RotatedCropBox]] = []
        solid_work_boxes: list[RotatedCropBox] = []
        if printed_fragments:
            solid_work_boxes = [
                RotatedCropBox(
                    center_x=b.center_x * scale,
                    center_y=b.center_y * scale,
                    width=b.width * scale,
                    height=b.height * scale,
                    angle_deg=b.angle_deg,
                    confidence=b.confidence,
                    source_width=work_w,
                    source_height=work_h,
                )
                for b in solid_candidates
            ]
            raw_printed_subblocks: list[RotatedCropBox] = []
            merged_pairs = self._merge_same_sheet_printed_fragments(
                work_bgr,
                printed_fragments,
                solid_work_boxes,
                min_side_work=min_side_work,
                dpi=float(dpi),
                scale=float(scale),
                out_raw_subblocks=raw_printed_subblocks,
            )
            for raw_wb, exp_wb in merged_pairs:
                merged_printed_raw.append(
                    RotatedCropBox(
                        center_x=raw_wb.center_x * inv_scale,
                        center_y=raw_wb.center_y * inv_scale,
                        width=raw_wb.width * inv_scale,
                        height=raw_wb.height * inv_scale,
                        angle_deg=raw_wb.angle_deg,
                        confidence=raw_wb.confidence,
                        index=0,
                        source_width=orig_w,
                        source_height=orig_h,
                        source_dpi=float(dpi),
                    )
                )
                merged_printed.append(
                    RotatedCropBox(
                        center_x=exp_wb.center_x * inv_scale,
                        center_y=exp_wb.center_y * inv_scale,
                        width=exp_wb.width * inv_scale,
                        height=exp_wb.height * inv_scale,
                        angle_deg=exp_wb.angle_deg,
                        confidence=exp_wb.confidence,
                        index=0,
                        source_width=orig_w,
                        source_height=orig_h,
                        source_dpi=float(dpi),
                    )
                )

        # Explicit `receipt_multi` preset: preserve the exact receipt & multi-slip pipeline
        # without any full-page fallback or standard-sheet override.
        if preset == "receipt_multi":
            self._last_resolved_preset = "receipt_multi"
            candidates = solid_candidates + merged_printed
            filtered = self._suppress_contained_boxes(candidates)
            if not filtered:
                return []
            if not self.config.multi_document:
                largest = max(filtered, key=lambda b: b.area)
                return [replace(largest, index=1)]
            sorted_boxes = self._sort_reading_order(filtered)
            return [replace(box, index=i) for i, box in enumerate(sorted_boxes, start=1)]

        # Explicit `standard_sheet` preset: snap single/standard paper sheets on a larger platen
        # (e.g. A4 placed on an A3 platen) to their physical paper-to-platen step boundaries.
        if preset == "standard_sheet":
            self._last_resolved_preset = "standard_sheet"
            std_boxes = self._build_standard_sheet_candidates(
                work_bgr,
                merged_pairs,
                solid_work_boxes,
                inv_scale=inv_scale,
                orig_w=orig_w,
                orig_h=orig_h,
                dpi=dpi,
                scale=scale,
                unify_single_sheet=True,
                subblocks=raw_printed_subblocks,
            )
            candidates = (
                std_boxes
                if (len(std_boxes) == 1 and std_boxes[0].area >= work_area * (inv_scale**2) * 0.18)
                else (solid_candidates + std_boxes)
            )
            filtered = self._suppress_contained_boxes(candidates)
            if not filtered:
                return []
            if not self.config.multi_document:
                largest = max(filtered, key=lambda b: b.area)
                return [replace(largest, index=1)]
            sorted_boxes = self._sort_reading_order(filtered)
            return [replace(box, index=i) for i, box in enumerate(sorted_boxes, start=1)]

        # `auto` preset:
        # 1) Evaluate full-page paper fallback using unpadded boxes so inner printed boundaries
        #    (table borders / text edges) are inspected directly before outer margin padding.
        raw_filtered = self._suppress_contained_boxes(solid_candidates + merged_printed_raw)
        if (
            raw_filtered
            and self.config.full_page_paper_fallback
            and self._is_printed_content_on_full_page(work_bgr, raw_filtered, scale)
        ):
            self._last_resolved_preset = "full_page"
            return [
                RotatedCropBox(
                    center_x=orig_w * 0.5,
                    center_y=orig_h * 0.5,
                    width=float(orig_w),
                    height=float(orig_h),
                    angle_deg=0.0,
                    confidence=1.0,
                    index=1,
                    source_width=orig_w,
                    source_height=orig_h,
                    source_dpi=float(dpi),
                )
            ]

        # 2) Check whether the scan is a single standard paper sheet (e.g. A4/B5) placed on a
        #    larger white platen (e.g. A3) with clear paper-to-platen step boundaries.
        unified_sheet_candidate = (
            self._find_auto_standard_sheet_group(
                work_bgr,
                merged_pairs,
                solid_work_boxes,
                subblocks=raw_printed_subblocks,
            )
            if is_light_platen and (merged_pairs or solid_work_boxes)
            else None
        )
        if unified_sheet_candidate is not None:
            self._last_resolved_preset = "standard_sheet"
            std_boxes = self._build_standard_sheet_candidates(
                work_bgr,
                [unified_sheet_candidate],
                [],
                inv_scale=inv_scale,
                orig_w=orig_w,
                orig_h=orig_h,
                dpi=dpi,
                scale=scale,
                unify_single_sheet=False,
            )
            filtered = self._suppress_contained_boxes(std_boxes)
            if filtered:
                return [replace(box, index=i) for i, box in enumerate(filtered, start=1)]

        # 3) Otherwise use the receipt & multi-document detection result.
        self._last_resolved_preset = "receipt_multi"
        candidates = solid_candidates + merged_printed
        filtered = self._suppress_contained_boxes(candidates)
        if not filtered:
            if self.config.full_page_paper_fallback and self._has_full_page_printed_ink(work_bgr):
                self._last_resolved_preset = "full_page"
                return [
                    RotatedCropBox(
                        center_x=orig_w * 0.5,
                        center_y=orig_h * 0.5,
                        width=float(orig_w),
                        height=float(orig_h),
                        angle_deg=0.0,
                        confidence=1.0,
                        index=1,
                        source_width=orig_w,
                        source_height=orig_h,
                        source_dpi=float(dpi),
                    )
                ]
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

    def _clamped_sensitivity(self) -> int:
        return max(-5, min(5, int(self.config.detection_sensitivity)))

    def _build_foreground_mask(self, bgr: np.ndarray) -> np.ndarray:
        """Build a clean binary mask separating documents from the platen background."""
        h, w = bgr.shape[:2]
        s = self._clamped_sensitivity()
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

        if s >= 0:
            min_thresh = max(2.0, self.config.color_diff_min_threshold - s * 1.5)
            mad_scale = max(1.8, self.config.color_diff_mad_scale - s * 0.5)
            base_offset = max(1.0, 4.0 - s * 0.6)
        else:
            min_thresh = self.config.color_diff_min_threshold - s * 1.6
            mad_scale = self.config.color_diff_mad_scale - s * 0.5
            base_offset = 4.0

        threshold = max(
            min_thresh,
            bg_sigma * mad_scale + base_offset,
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

        # On a uniform bright white platen (typical flatbed cover), thin printed text on white
        # thermal receipts can be eroded away by `open_kernel` above. Preserve genuine dark ink
        # clusters with a dedicated text-grouping mask (ignoring outer scanner frame artifacts).
        bg_l = float(bg_lab[0])
        bg_chroma = math.hypot(float(bg_lab[1]) - 128.0, float(bg_lab[2]) - 128.0)
        if bg_l >= 210.0 and bg_chroma <= 22.0 and bg_sigma <= 3.0:
            raw_lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
            ink_drop = max(28.0, bg_sigma * 6.0 + 20.0)
            text_mask = ((bg_l - raw_lab[:, :, 0].astype(np.float32)) >= ink_drop).astype(
                np.uint8
            ) * 255
            border_trim = max(4, min(10, int(round(min_dim * 0.006))))
            text_mask[:border_trim, :] = 0
            text_mask[-border_trim:, :] = 0
            text_mask[:, :border_trim] = 0
            text_mask[:, -border_trim:] = 0

            # Remove 1-pixel dust specks, then close intra-word and intra-line gaps.
            text_mask = cv2.morphologyEx(
                text_mask,
                cv2.MORPH_OPEN,
                cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)),
            )
            text_close_k = max(7, min(15, int(round(min_dim * 0.014)) | 1))
            text_mask = cv2.morphologyEx(
                text_mask,
                cv2.MORPH_CLOSE,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (text_close_k, text_close_k)),
            )
            color_mask = cv2.bitwise_or(color_mask, text_mask)

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
        if s >= 0:
            grad_floor = max(7.0, 22.0 - s * 3.0)
            grad_mult = max(1.2, 2.4 - s * 0.24)
            grad_bias = max(2.0, 10.0 - s * 1.6)
        else:
            grad_floor = 22.0 - s * 3.0
            grad_mult = 2.4 - s * 0.2
            grad_bias = 10.0 - s * 1.5
        grad_high = max(grad_floor, float(np.percentile(border_grad, 98)) * grad_mult + grad_bias)
        grad_low = max(3.5 if s > 0 else 10.0, grad_high * 0.45)
        edge_mask = cv2.Canny(
            gray,
            int(round(grad_low)),
            int(round(grad_high)),
            apertureSize=3,
        )

        # Suppress outer scanner frame border artifacts.
        edge_trim = max(4, min(10, int(round(min_dim * 0.006))))
        edge_mask[:edge_trim, :] = 0
        edge_mask[-edge_trim:, :] = 0
        edge_mask[:, :edge_trim] = 0
        edge_mask[:, -edge_trim:] = 0

        edge_close_k = max(5, int(round(min_dim * 0.01)) | 1)
        edge_close_k = min(edge_close_k, 13)
        edge_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (edge_close_k, edge_close_k)
        )
        edge_closed = cv2.morphologyEx(edge_mask, cv2.MORPH_CLOSE, edge_kernel)
        edge_filled = self._fill_external_contours(edge_closed)

        # Integrate edge-derived components:
        # 1) Include components not detected by color_filled (e.g. white paper on white platen).
        # 2) Also include rectangular outer paper contours that meaningfully enclose a smaller
        #    color_filled region (e.g. printed text/tables inside a white sheet whose outer edge
        #    was caught by Canny), without bloating dark/colored-paper masks that already match.
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
            overlap_ratio = overlap / area
            if overlap_ratio < 0.35:
                combined = cv2.bitwise_or(combined, comp_mask)
                continue

            # Check if this edge contour is a clean outer paper boundary enclosing inner ink.
            if overlap_ratio < 0.88 and area >= overlap * 1.15:
                _, (rw, rh), _ = cv2.minAreaRect(cnt)
                rect_area = float(rw * rh)
                if rect_area > 0.0 and (area / rect_area) >= 0.72:
                    combined = cv2.bitwise_or(combined, comp_mask)

        combined = cv2.morphologyEx(combined, cv2.MORPH_OPEN, open_kernel)
        return self._fill_external_contours(combined)

    def _is_white_sheet_printed_fragment(
        self,
        work_lab: np.ndarray,
        bg_lab: np.ndarray,
        box: RotatedCropBox,
    ) -> bool:
        """Return True if `box` is a printed block on white paper rather than a solid sheet."""
        h, w = work_lab.shape[:2]
        bg_l = float(bg_lab[0])
        pts = box.corners()
        poly = np.round(pts).astype(np.int32)
        box_mask = np.zeros((h, w), dtype=np.uint8)
        cv2.fillConvexPoly(box_mask, poly, 255)
        inside_pixels = work_lab[box_mask > 0].astype(np.float32)
        if len(inside_pixels) < 30:
            return False

        # Check for dark printed ink sitting right on the box boundary (text/logo/barcode/rule)
        # as opposed to a faint physical paper-edge shadow around blank white margins.
        rim_px = max(3, min(10, int(round(min(box.width, box.height) * 0.14))))
        rim_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (rim_px * 2 + 1, rim_px * 2 + 1))
        eroded = cv2.erode(box_mask, rim_kernel)
        inner_rim = cv2.subtract(box_mask, eroded)
        rim_l = work_lab[:, :, 0][inner_rim > 0].astype(np.float32)
        if rim_l.size == 0:
            return False

        # Require genuine dark printed ink sitting right on the box boundary so faint physical
        # paper-edge shadows (~20-38 drop) stay solid sheets. Thin 100-DPI anti-aliased text
        # lines (<= 22 px thick on bright white platens) have slightly softer L-drops (~32-45).
        is_thin_text_line = min(box.width, box.height) <= 22.0 and bg_l >= 235.0
        ink_drop = (
            max(32.0, self.config.printed_border_ink_drop_l - 8.0)
            if is_thin_text_line
            else max(48.0, self.config.printed_border_ink_drop_l + 8.0)
        )
        ink_ratio = float(np.count_nonzero((bg_l - rim_l) >= ink_drop)) / float(rim_l.size)
        if ink_ratio < 0.03:
            return False

        # Verify interior paper ground color matches the bright platen (white/thermal paper).
        l_cutoff = float(np.percentile(inside_pixels[:, 0], 88))
        bright_inside = inside_pixels[inside_pixels[:, 0] >= l_cutoff]
        if len(bright_inside) == 0:
            return False
        paper_lab = np.median(bright_inside, axis=0)
        paper_l = float(paper_lab[0])
        paper_chroma = math.hypot(float(paper_lab[1]) - 128.0, float(paper_lab[2]) - 128.0)
        delta_e_paper = float(
            math.sqrt(
                ((paper_l - bg_l) * 1.15) ** 2
                + (float(paper_lab[1]) - float(bg_lab[1])) ** 2
                + (float(paper_lab[2]) - float(bg_lab[2])) ** 2
            )
        )
        if delta_e_paper <= self.config.paper_color_match_max_delta_e:
            return True

        # Also treat compact neutral dark-ink elements (inverted promo banners, solid logos,
        # QR blocks) or large neutral light-gray shaded tables bordered by dark rules as
        # printed fragments on a white sheet.
        box_area_ratio = box.area / float(h * w)
        if paper_l < 140.0 and paper_chroma <= 18.0 and box_area_ratio < 0.04:
            return True
        return (
            paper_l >= 180.0
            and paper_chroma <= 14.0
            and ink_ratio >= 0.08
            and box_area_ratio >= 0.08
        )

    def _merge_same_sheet_printed_fragments(
        self,
        work_bgr: np.ndarray,
        fragments: list[tuple[RotatedCropBox, np.ndarray]],
        solid_boxes: list[RotatedCropBox],
        *,
        min_side_work: float,
        dpi: float = 0.0,
        scale: float = 1.0,
        out_raw_subblocks: list[RotatedCropBox] | None = None,
    ) -> list[tuple[RotatedCropBox, RotatedCropBox]]:
        """Cluster nearby aligned printed blocks on white sheets into `(raw, expanded)` boxes."""
        n = len(fragments)
        if n == 0:
            return []

        h, w = work_bgr.shape[:2]
        work_area = float(h * w)
        max_dim = float(max(h, w))

        rects = [box.bounding_rect() for box, _ in fragments]
        solid_rects = [b.bounding_rect() for b in solid_boxes]

        parent = list(range(n))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

        def bridge_hits_solid(x1: float, y1: float, x2: float, y2: float) -> bool:
            for sx, sy, sw, sh in solid_rects:
                sx2, sy2 = sx + sw, sy + sh
                if min(x2, sx2) > max(x1, sx) and min(y2, sy2) > max(y1, sy):
                    return True
            return False

        for i in range(n):
            ax, ay, aw, ah = rects[i]
            ax2, ay2 = ax + aw, ay + ah
            for j in range(i + 1, n):
                bx, by, bw, bh = rects[j]
                bx2, by2 = bx + bw, by + bh

                ov_x = min(ax2, bx2) - max(ax, bx)
                ov_y = min(ay2, by2) - max(ay, by)
                gap_x = max(0.0, max(ax - bx2, bx - ax2))
                gap_y = max(0.0, max(ay - by2, by - ay2))

                # Vertical stacking (upright/upside-down receipt parts aligned along X).
                min_w = max(1.0, min(aw, bw))
                max_w = max(aw, bw)
                max_gap_y = max(24.0, min(max_w * 0.72, max_dim * 0.085))
                min_ov_x = -min_w * 0.6 if (min_w <= 16.0 and gap_y <= 28.0) else min_w * 0.25
                same_column = ov_x >= min_ov_x and gap_y <= max_gap_y

                # Horizontal stacking (sideways 90°/270° receipt parts aligned along Y).
                min_h = max(1.0, min(ah, bh))
                max_h = max(ah, bh)
                max_gap_x = max(24.0, min(max_h * 0.72, max_dim * 0.085))
                min_ov_y = -min_h * 0.6 if (min_h <= 16.0 and gap_x <= 28.0) else min_h * 0.25
                same_row = ov_y >= min_ov_y and gap_x <= max_gap_x

                if not (same_column or same_row):
                    continue

                ux1, uy1 = min(ax, bx), min(ay, by)
                ux2, uy2 = max(ax2, bx2), max(ay2, by2)
                if bridge_hits_solid(ux1, uy1, ux2, uy2):
                    continue
                union(i, j)

        groups: dict[int, list[int]] = {}
        for idx in range(n):
            groups.setdefault(find(idx), []).append(idx)

        work_gray = cv2.cvtColor(work_bgr, cv2.COLOR_BGR2GRAY)
        raw_groups: list[RotatedCropBox] = []

        for member_indices in groups.values():
            all_pts = np.vstack([fragments[idx][1] for idx in member_indices])
            rect = cv2.minAreaRect(all_pts)
            cx, cy, rw, rh, angle_deg = _normalize_min_area_rect(rect)
            if rw <= 0.0 or rh <= 0.0:
                continue

            if len(member_indices) == 1:
                group_conf = fragments[member_indices[0]][0].confidence
            else:
                total_ca = sum(float(cv2.contourArea(fragments[idx][1])) for idx in member_indices)
                group_conf = round(min(1.0, total_ca / float(rw * rh)), 4)

            raw_groups.append(
                RotatedCropBox(
                    center_x=cx,
                    center_y=cy,
                    width=rw,
                    height=rh,
                    angle_deg=angle_deg,
                    confidence=group_conf,
                    source_width=w,
                    source_height=h,
                )
            )

        # Expand each printed group to faint physical paper-edge shadows and trace detected
        # shadow lines tangentially to the sheet's true corners. Only guard against collisions
        # with confirmed solid sheets so small sibling fragments on the same sheet (e.g. left
        # label column vs right value column) never block tracing and can merge cleanly below.
        if out_raw_subblocks is not None:
            border_guard = max(4.0, float(min(h, w)) * 0.008)
            for rg in raw_groups:
                rx, ry, rw, rh = rg.bounding_rect()
                touches_frame = (
                    rx <= border_guard
                    or ry <= border_guard
                    or (rx + rw) >= (float(w) - border_guard)
                    or (ry + rh) >= (float(h) - border_guard)
                )
                if touches_frame and min(rg.width, rg.height) <= 12.0:
                    continue
                if (
                    rg.area < 250.0
                    or min(rg.width, rg.height) < 8.0
                    or max(rg.width, rg.height) < 24.0
                ):
                    continue
                out_raw_subblocks.append(rg)

        expanded_groups: list[tuple[RotatedCropBox, RotatedCropBox]] = []
        for raw_group in raw_groups:
            expanded = self._expand_printed_group_to_paper_edges(
                work_gray,
                raw_group,
                solid_boxes=solid_boxes,
                dpi=dpi,
                scale=scale,
                aspect_ratio_completion=self.config.aspect_ratio_completion,
            )
            expanded_groups.append((raw_group, expanded))

        # Re-merge expanded printed groups that now overlap (e.g. two columns under the same
        # traced top shadow line) so sibling fragments unify into one complete sheet box.
        merged_pairs = self._unify_overlapping_printed_groups(
            expanded_groups,
            work_w=w,
            work_h=h,
            work_area=work_area,
            min_side_work=min_side_work,
        )
        return merged_pairs

    def _unify_overlapping_printed_groups(
        self,
        group_pairs: list[tuple[RotatedCropBox, RotatedCropBox]],
        *,
        work_w: int,
        work_h: int,
        work_area: float,
        min_side_work: float,
    ) -> list[tuple[RotatedCropBox, RotatedCropBox]]:
        """Unify expanded printed groups that overlap after shadow-line expansion."""
        m = len(group_pairs)
        if m == 0:
            return []

        parent = list(range(m))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i: int, j: int) -> None:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[ri] = rj

        polys = [exp.corners() for _, exp in group_pairs]
        areas = [max(1.0, exp.area) for _, exp in group_pairs]
        for i in range(m):
            for j in range(i + 1, m):
                inter, _ = cv2.intersectConvexConvex(polys[i], polys[j])
                if inter <= 0.0:
                    continue
                min_area = min(areas[i], areas[j])
                if (inter / min_area) >= 0.25:
                    union(i, j)

        clusters: dict[int, list[int]] = {}
        for idx in range(m):
            clusters.setdefault(find(idx), []).append(idx)

        result: list[tuple[RotatedCropBox, RotatedCropBox]] = []
        for indices in clusters.values():
            if len(indices) == 1:
                raw_b, exp_b = group_pairs[indices[0]]
            else:
                # Preserve angle of the dominant group and enclose all member corners.
                dominant_idx = max(indices, key=lambda k: group_pairs[k][1].area)
                dom_angle = group_pairs[dominant_idx][1].angle_deg
                raw_b = self._enclose_boxes_at_angle(
                    [group_pairs[k][0] for k in indices],
                    angle_deg=dom_angle,
                    work_w=work_w,
                    work_h=work_h,
                )
                exp_b = self._enclose_boxes_at_angle(
                    [group_pairs[k][1] for k in indices],
                    angle_deg=dom_angle,
                    work_w=work_w,
                    work_h=work_h,
                )

            area_ratio = exp_b.area / work_area
            if area_ratio < self.config.min_area_ratio or area_ratio > self.config.max_area_ratio:
                continue
            if min(exp_b.width, exp_b.height) < min_side_work:
                continue
            result.append((raw_b, exp_b))
        return result

    @staticmethod
    def _enclose_boxes_at_angle(
        boxes: Sequence[RotatedCropBox],
        *,
        angle_deg: float,
        work_w: int,
        work_h: int,
    ) -> RotatedCropBox:
        """Build the tightest `RotatedCropBox` at `angle_deg` enclosing all `boxes`."""
        pts = np.vstack([b.corners() for b in boxes])
        rad = math.radians(angle_deg)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)

        # Project corners into the frame rotated by `angle_deg`.
        u = pts[:, 0] * cos_a + pts[:, 1] * sin_a
        v = -pts[:, 0] * sin_a + pts[:, 1] * cos_a
        u_min, u_max = float(np.min(u)), float(np.max(u))
        v_min, v_max = float(np.min(v)), float(np.max(v))

        uc = (u_min + u_max) * 0.5
        vc = (v_min + v_max) * 0.5
        cx = uc * cos_a - vc * sin_a
        cy = uc * sin_a + vc * cos_a

        return RotatedCropBox(
            center_x=cx,
            center_y=cy,
            width=min(float(work_w), max(1.0, u_max - u_min)),
            height=min(float(work_h), max(1.0, v_max - v_min)),
            angle_deg=angle_deg,
            confidence=0.85,
            source_width=work_w,
            source_height=work_h,
        )

    @classmethod
    def _expand_printed_group_to_paper_edges(
        cls,
        work_gray: np.ndarray,
        box: RotatedCropBox,
        *,
        solid_boxes: Sequence[RotatedCropBox] = (),
        dpi: float = 0.0,
        scale: float = 1.0,
        aspect_ratio_completion: bool = True,
    ) -> RotatedCropBox:
        """Snap a printed-fragment box outward to faint paper edges and trace shadow lines."""
        h, w = work_gray.shape[:2]
        max_dim = float(max(h, w))
        short_side = min(box.width, box.height)
        long_side = max(box.width, box.height)

        # Physical paper margins (e.g. 10-18mm top header margin above a title) do not shrink
        # when a slip is partially clipped by the scan region, so keep `max_normal` generous
        # relative to image scale while guarding against adjacent solid boxes.
        max_normal = int(round(max(48.0, min(max(short_side * 0.9, 52.0), max_dim * 0.085))))
        max_tangent = int(round(max(60.0, min(max(long_side * 1.1, 140.0), max_dim * 0.25))))
        default_pad = max(4.0, min(short_side * 0.045, max_dim * 0.009))

        if box.width < 4.0 or box.height < 4.0:
            return box

        # Build a single deskewed local patch aligned with `box.angle_deg` so tangential edge
        # tracing works identically on upright and arbitrarily tilted documents.
        pad_x = max(max_normal, max_tangent) + 12
        pad_y = max(max_normal, max_tangent) + 12
        patch_w = int(round(box.width)) + pad_x * 2
        patch_h = int(round(box.height)) + pad_y * 2

        rot_mat = cv2.getRotationMatrix2D((box.center_x, box.center_y), box.angle_deg, 1.0)
        rot_mat[0, 2] += (patch_w * 0.5) - box.center_x
        rot_mat[1, 2] += (patch_h * 0.5) - box.center_y
        inv_mat = cv2.invertAffineTransform(rot_mat)

        bg_fill = int(round(float(np.percentile(work_gray, 95))))
        patch = cv2.warpAffine(
            work_gray,
            rot_mat,
            (patch_w, patch_h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=bg_fill,
        ).astype(np.float32)

        x1 = int(round(pad_x))
        y1 = int(round(pad_y))
        x2 = int(round(pad_x + box.width))
        y2 = int(round(pad_y + box.height))

        solid_rects = [b.bounding_rect() for b in solid_boxes]
        collision_margin = 6.0

        def local_point_valid(lx: float, ly: float) -> bool:
            src_x = inv_mat[0, 0] * lx + inv_mat[0, 1] * ly + inv_mat[0, 2]
            src_y = inv_mat[1, 0] * lx + inv_mat[1, 1] * ly + inv_mat[1, 2]
            if src_x < 2.0 or src_x > (w - 3.0) or src_y < 2.0 or src_y > (h - 3.0):
                return False
            for ox, oy, ow, oh in solid_rects:
                if (
                    (ox - collision_margin) <= src_x <= (ox + ow + collision_margin)
                    and (oy - collision_margin) <= src_y <= (oy + oh + collision_margin)
                ):
                    return False
            return True

        shadow_drop_thresh = 4.0

        def has_horizontal_shadow_support(edge_y: float) -> bool:
            yi = int(round(edge_y))
            if yi < 6 or yi >= patch_h - 6 or x2 <= x1 + 8:
                return False
            strip = np.min(patch[yi - 2 : yi + 3, x1 : x2 + 1], axis=0)
            above = float(np.median(patch[yi - 6 : yi - 3, x1 : x2 + 1]))
            below = float(np.median(patch[yi + 4 : yi + 7, x1 : x2 + 1]))
            local_bg = max(above, below)
            hits = np.count_nonzero((local_bg - strip) >= shadow_drop_thresh)
            return (float(hits) / float(strip.size)) >= 0.45

        def has_vertical_shadow_support(edge_x: float) -> bool:
            xi = int(round(edge_x))
            if xi < 6 or xi >= patch_w - 6 or y2 <= y1 + 8:
                return False
            strip = np.min(patch[y1 : y2 + 1, xi - 2 : xi + 3], axis=1)
            left_med = float(np.median(patch[y1 : y2 + 1, xi - 6 : xi - 3]))
            right_med = float(np.median(patch[y1 : y2 + 1, xi + 4 : xi + 7]))
            local_bg = max(left_med, right_med)
            hits = np.count_nonzero((local_bg - strip) >= shadow_drop_thresh)
            return (float(hits) / float(strip.size)) >= 0.45

        def find_normal_edge(
            profile: np.ndarray,
            *,
            inner_profile: np.ndarray,
            check_valid: Callable[[int], bool],
            check_inner_line_support: Callable[[int], bool],
        ) -> float | None:
            if profile.size < 6:
                return None
            baseline = float(np.percentile(profile, 85))
            is_bordered_card = box.confidence >= 0.90

            # 1a) For high-rectangularity bordered cards (e.g. postcards with printed frames
            #     extending to all 4 edges), check `profile[2:7]` for clean outer platen and snap
            #     1px inside the absorbed perimeter shadow or directly onto the outer border edge.
            if is_bordered_card and inner_profile.size >= 8:
                card_outer = (
                    profile[2 : min(7, profile.size)]
                    if profile.size >= 4
                    else profile[: min(5, profile.size)]
                )
                if float(np.min(card_outer)) >= (baseline - 3.5):
                    for k in range(0, min(6, inner_profile.size - 4)):
                        val_k = float(inner_profile[k])
                        if (
                            k > 0
                            and float(inner_profile[k - 1]) < (baseline - 4.0)
                            and val_k > float(inner_profile[k - 1]) + 1.5
                        ):
                            break
                        if (baseline - val_k) < 4.0:
                            continue
                        if k + 1 < inner_profile.size and float(inner_profile[k + 1]) < val_k - 0.5:
                            continue
                        behind = inner_profile[k + 1 : min(inner_profile.size, k + 7)]
                        if behind.size >= 1:
                            behind_peak = float(np.max(behind))
                            if (
                                (behind_peak >= (baseline - 5.0) and (behind_peak - val_k) >= 3.0)
                                or (
                                    behind_peak >= (baseline - 12.0)
                                    and (behind_peak - val_k) >= 8.0
                                )
                            ):
                                return -float(k + 1)
                    if (
                        (baseline - float(inner_profile[0])) <= 12.0
                        and (baseline - float(np.min(inner_profile[2:7]))) >= 20.0
                    ):
                        return -2.0

            # 1b) Standard absorbed paper-edge shadow check for receipts and white slips.
            if inner_profile.size >= 8:
                inner_outer = (
                    profile[1 : min(6, profile.size)]
                    if profile.size >= 3
                    else profile[: min(5, profile.size)]
                )
                outer_clean = float(np.min(inner_outer)) >= (baseline - 3.5)
                if outer_clean:
                    max_in = min(14, inner_profile.size - 4)
                    dark_run = 0
                    for k in range(0, max_in):
                        val_k = float(inner_profile[k])
                        # Allow descending a 2-4px wide shadow valley slope, but stop if a dark
                        # region is too wide (> 4px, i.e. printed text/banner) or starts rising.
                        if (
                            k > 0
                            and float(inner_profile[k - 1]) < (baseline - 4.0)
                            and val_k > float(inner_profile[k - 1]) + 1.5
                        ):
                            break
                        drop_k = baseline - val_k
                        if drop_k < 4.0:
                            dark_run = 0
                            continue
                        dark_run += 1
                        if dark_run > 4:
                            break
                        # Wait until the local minimum of the absorbed shadow valley.
                        if k + 1 < inner_profile.size and float(inner_profile[k + 1]) < val_k - 0.5:
                            continue
                        # Confirm it is a thin shadow valley with white paper margin behind it
                        # (not a solid printed banner or dense text) AND has continuous shadow
                        # line support along the edge (not isolated sparse bullets like "1, 2, 3").
                        behind = inner_profile[k + 2 : min(inner_profile.size, k + 7)]
                        if behind.size < 2:
                            continue
                        behind_white = float(np.max(behind))
                        if (
                            behind_white >= (baseline - 5.0)
                            and (behind_white - val_k) >= 3.0
                            and check_inner_line_support(k)
                        ):
                            return -float(k)

            # 2) Otherwise scan outward for a thin paper-edge shadow valley (dark dip flanked on
            #    both inner and outer sides by bright white paper/platen).
            best_valley_pos: int | None = None
            best_valley_score = 0.0
            best_fallback_pos: int | None = None
            best_fallback_score = 0.0

            for idx in range(3, len(profile) - 2):
                if not check_valid(idx):
                    break
                val = float(profile[idx])
                drop = baseline - val
                if drop < 3.0:
                    continue

                # Check inner and outer flanks 2-4px away to confirm a thin shadow line.
                in_hi = max(0, idx - 2)
                in_lo = max(0, idx - 5)
                out_lo = min(len(profile), idx + 2)
                out_hi = min(len(profile), idx + 6)
                in_flank = float(np.max(profile[in_lo : in_hi + 1])) if in_hi >= in_lo else baseline
                out_flank = (
                    float(np.max(profile[out_lo:out_hi])) if out_hi > out_lo else baseline
                )
                valley_depth = min(in_flank - val, out_flank - val)

                # Stop scanning outward if we hit a dense dark object (another document).
                if baseline - out_flank >= 20.0:
                    break

                if valley_depth >= 2.5 and drop >= 3.5:
                    if valley_depth > best_valley_score:
                        best_valley_score = valley_depth
                        best_valley_pos = idx
                elif drop >= 4.0:
                    if drop > best_fallback_score:
                        best_fallback_score = drop
                        best_fallback_pos = idx

            if best_valley_pos is not None:
                return float(best_valley_pos)
            return float(best_fallback_pos) if best_fallback_pos is not None else None

        # Step 1: Normal outward edge search on all 4 sides in the deskewed local patch.
        l_prof = np.mean(patch[y1 : y2 + 1, max(0, x1 - max_normal) : x1 + 1], axis=0)[::-1]
        r_prof = np.mean(patch[y1 : y2 + 1, x2 : min(patch_w, x2 + max_normal + 1)], axis=0)
        t_prof = np.mean(patch[max(0, y1 - max_normal) : y1 + 1, x1 : x2 + 1], axis=1)[::-1]
        b_prof = np.mean(patch[y2 : min(patch_h, y2 + max_normal + 1), x1 : x2 + 1], axis=1)

        in_depth = 20
        l_in = np.mean(patch[y1 : y2 + 1, x1 : min(patch_w, x1 + in_depth)], axis=0)
        r_in = np.mean(patch[y1 : y2 + 1, max(0, x2 - in_depth + 1) : x2 + 1], axis=0)[::-1]
        t_in = np.mean(patch[y1 : min(patch_h, y1 + in_depth), x1 : x2 + 1], axis=1)
        b_in = np.mean(patch[max(0, y2 - in_depth + 1) : y2 + 1, x1 : x2 + 1], axis=1)[::-1]

        mid_x = (x1 + x2) * 0.5
        mid_y = (y1 + y2) * 0.5
        off_l = find_normal_edge(
            l_prof,
            inner_profile=l_in,
            check_valid=lambda d: local_point_valid(float(x1 - d), mid_y),
            check_inner_line_support=lambda k: has_vertical_shadow_support(float(x1 + k)),
        )
        off_r = find_normal_edge(
            r_prof,
            inner_profile=r_in,
            check_valid=lambda d: local_point_valid(float(x2 + d), mid_y),
            check_inner_line_support=lambda k: has_vertical_shadow_support(float(x2 - k)),
        )
        off_t = find_normal_edge(
            t_prof,
            inner_profile=t_in,
            check_valid=lambda d: local_point_valid(mid_x, float(y1 - d)),
            check_inner_line_support=lambda k: has_horizontal_shadow_support(float(y1 + k)),
        )
        off_b = find_normal_edge(
            b_prof,
            inner_profile=b_in,
            check_valid=lambda d: local_point_valid(mid_x, float(y2 + d)),
            check_inner_line_support=lambda k: has_horizontal_shadow_support(float(y2 - k)),
        )

        pad_l = off_l if off_l is not None else default_pad
        pad_r = off_r if off_r is not None else default_pad
        pad_t = off_t if off_t is not None else default_pad
        pad_b = off_b if off_b is not None else default_pad

        # Step 2: Tangential edge-line span tracing.
        # Verify the detected normal edge is a genuine continuous thin shadow along the current
        # side (>= 45% coverage), then trace it outward to the sheet's physical corners.
        max_gap_px = max(12, int(round(max_dim * 0.016)))

        def trace_horizontal_edge(edge_y: float, start_x: int, step: int, outward_dy: int) -> float:
            """Trace a horizontal shadow line outward from `start_x` in direction `step` (±1)."""
            cur_y = float(edge_y)
            y_min_bound = max(6.0, edge_y - 5.0)
            y_max_bound = min(float(patch_h - 7), edge_y + 5.0)
            last_hit_dist = 0
            gap = 0
            for dist in range(1, max_tangent + 1):
                tx = start_x + step * dist
                if tx < 2 or tx >= patch_w - 2:
                    break
                if not local_point_valid(float(tx), cur_y):
                    break

                yi = int(round(cur_y))
                band = patch[max(0, yi - 2) : min(patch_h, yi + 3), tx]
                if band.size == 0:
                    break
                min_idx = int(np.argmin(band))
                line_val = float(band[min_idx])

                out_y0 = max(0, min(patch_h - 1, yi + outward_dy * 4))
                out_y1 = max(0, min(patch_h - 1, yi + outward_dy * 6))
                in_y0 = max(0, min(patch_h - 1, yi - outward_dy * 4))
                in_y1 = max(0, min(patch_h - 1, yi - outward_dy * 6))
                out_lo, out_hi = min(out_y0, out_y1), max(out_y0, out_y1) + 1
                in_lo, in_hi = min(in_y0, in_y1), max(in_y0, in_y1) + 1
                out_med = float(np.median(patch[out_lo:out_hi, tx]))
                in_med = float(np.median(patch[in_lo:in_hi, tx]))
                local_bg = max(out_med, in_med)

                # Stop if the outer flank itself turns dark (entering another document).
                if local_bg - out_med >= 18.0:
                    break

                if local_bg - line_val >= shadow_drop_thresh:
                    last_hit_dist = dist
                    gap = 0
                    target_y = float(max(0, yi - 2) + min_idx)
                    step_y = float(np.clip(target_y - cur_y, -0.35, 0.35))
                    cur_y = float(np.clip(cur_y + step_y, y_min_bound, y_max_bound))
                else:
                    gap += 1
                    if gap > max_gap_px:
                        break
            return float(last_hit_dist)

        def trace_vertical_edge(edge_x: float, start_y: int, step: int, outward_dx: int) -> float:
            """Trace a vertical shadow line outward from `start_y` in direction `step` (±1)."""
            cur_x = float(edge_x)
            x_min_bound = max(6.0, edge_x - 5.0)
            x_max_bound = min(float(patch_w - 7), edge_x + 5.0)
            last_hit_dist = 0
            gap = 0
            for dist in range(1, max_tangent + 1):
                ty = start_y + step * dist
                if ty < 2 or ty >= patch_h - 2:
                    break
                if not local_point_valid(cur_x, float(ty)):
                    break

                xi = int(round(cur_x))
                band = patch[ty, max(0, xi - 2) : min(patch_w, xi + 3)]
                if band.size == 0:
                    break
                min_idx = int(np.argmin(band))
                line_val = float(band[min_idx])

                out_x0 = max(0, min(patch_w - 1, xi + outward_dx * 4))
                out_x1 = max(0, min(patch_w - 1, xi + outward_dx * 6))
                in_x0 = max(0, min(patch_w - 1, xi - outward_dx * 4))
                in_x1 = max(0, min(patch_w - 1, xi - outward_dx * 6))
                out_lo, out_hi = min(out_x0, out_x1), max(out_x0, out_x1) + 1
                in_lo, in_hi = min(in_x0, in_x1), max(in_x0, in_x1) + 1
                out_med = float(np.median(patch[ty, out_lo:out_hi]))
                in_med = float(np.median(patch[ty, in_lo:in_hi]))
                local_bg = max(out_med, in_med)

                if local_bg - out_med >= 18.0:
                    break

                if local_bg - line_val >= shadow_drop_thresh:
                    last_hit_dist = dist
                    gap = 0
                    target_x = float(max(0, xi - 2) + min_idx)
                    step_x = float(np.clip(target_x - cur_x, -0.35, 0.35))
                    cur_x = float(np.clip(cur_x + step_x, x_min_bound, x_max_bound))
                else:
                    gap += 1
                    if gap > max_gap_px:
                        break
            return float(last_hit_dist)

        horiz_supported = False
        vert_supported = False

        if off_t is not None:
            edge_y_top = float(y1) - off_t
            if has_horizontal_shadow_support(edge_y_top):
                horiz_supported = True
                traced_l = trace_horizontal_edge(edge_y_top, x1, -1, outward_dy=-1)
                traced_r = trace_horizontal_edge(edge_y_top, x2, 1, outward_dy=-1)
                if traced_l > 3.0:
                    pad_l = max(pad_l, traced_l)
                if traced_r > 3.0:
                    pad_r = max(pad_r, traced_r)
        if off_b is not None:
            edge_y_bot = float(y2) + off_b
            if has_horizontal_shadow_support(edge_y_bot):
                horiz_supported = True
                traced_l = trace_horizontal_edge(edge_y_bot, x1, -1, outward_dy=1)
                traced_r = trace_horizontal_edge(edge_y_bot, x2, 1, outward_dy=1)
                if traced_l > 3.0:
                    pad_l = max(pad_l, traced_l)
                if traced_r > 3.0:
                    pad_r = max(pad_r, traced_r)
        if off_l is not None:
            edge_x_left = float(x1) - off_l
            if has_vertical_shadow_support(edge_x_left):
                vert_supported = True
                if off_t is None:
                    traced_t = trace_vertical_edge(edge_x_left, y1, -1, outward_dx=-1)
                    if traced_t > 3.0:
                        pad_t = max(pad_t, traced_t)
                if off_b is None:
                    traced_b = trace_vertical_edge(edge_x_left, y2, 1, outward_dx=-1)
                    if traced_b > 3.0:
                        pad_b = max(pad_b, traced_b)
        if off_r is not None:
            edge_x_right = float(x2) + off_r
            if has_vertical_shadow_support(edge_x_right):
                vert_supported = True
                if off_t is None:
                    traced_t = trace_vertical_edge(edge_x_right, y1, -1, outward_dx=1)
                    if traced_t > 3.0:
                        pad_t = max(pad_t, traced_t)
                if off_b is None:
                    traced_b = trace_vertical_edge(edge_x_right, y2, 1, outward_dx=1)
                    if traced_b > 3.0:
                        pad_b = max(pad_b, traced_b)

        # Step 3: Aspect-ratio completion anchored on high-confidence sides.
        # When a standard white document (e.g. a 100x148mm postcard) casts crisp shadows only on
        # one or two sides, the unanchored side stops near the edge of the ink. Anchor the
        # confident axis and extend unanchored edge(s) outward to the matched standard paper ratio.
        if aspect_ratio_completion:
            eff_dpi = float(dpi) * float(scale) if dpi > 0.0 and scale > 0.0 else 0.0
            work_area = float(h * w)
            pad_t, pad_r, pad_b, pad_l = cls._complete_margins_by_standard_ratio(
                patch=patch,
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                pad_t=pad_t,
                pad_r=pad_r,
                pad_b=pad_b,
                pad_l=pad_l,
                horiz_supported=horiz_supported,
                vert_supported=vert_supported,
                box_confidence=box.confidence,
                bg_fill=bg_fill,
                effective_dpi=eff_dpi,
                work_area=work_area,
                local_point_valid=local_point_valid,
            )

        # Clamp final paddings to stay strictly within source image boundaries.
        bx, by, bw, bh = box.bounding_rect()
        pad_l = min(pad_l, max(0.0, bx))
        pad_r = min(pad_r, max(0.0, float(w - 1) - (bx + bw)))
        pad_t = min(pad_t, max(0.0, by))
        pad_b = min(pad_b, max(0.0, float(h - 1) - (by + bh)))

        eff_cx, eff_cy, eff_w, eff_h, eff_angle = box.with_edge_margins(
            top=pad_t, right=pad_r, bottom=pad_b, left=pad_l, unit="px"
        ).resolved_geometry()
        return RotatedCropBox(
            center_x=eff_cx,
            center_y=eff_cy,
            width=min(float(w), eff_w),
            height=min(float(h), eff_h),
            angle_deg=eff_angle,
            confidence=box.confidence,
            source_width=w,
            source_height=h,
        )

    @staticmethod
    def _measure_patch_edge_coverage(
        patch: np.ndarray,
        lx1: int,
        ly1: int,
        lx2: int,
        ly2: int,
    ) -> tuple[float, float, float, float]:
        """Measure perimeter shadow/step support ratios `(top, right, bottom, left)`."""
        ph, pw = patch.shape[:2]
        lx1 = max(0, min(pw - 2, lx1))
        lx2 = max(lx1 + 1, min(pw - 1, lx2))
        ly1 = max(0, min(ph - 2, ly1))
        ly2 = max(ly1 + 1, min(ph - 1, ly2))

        edges = (
            (
                0,
                patch[max(0, ly1 - 2) : min(ph, ly1 + 3), lx1 : lx2 + 1],
                patch[max(0, ly1 - 7) : max(1, ly1 - 2), lx1 : lx2 + 1],
                patch[min(ph - 1, ly1 + 3) : min(ph, ly1 + 8), lx1 : lx2 + 1],
            ),
            (
                1,
                patch[ly1 : ly2 + 1, max(0, lx2 - 2) : min(pw, lx2 + 3)],
                patch[ly1 : ly2 + 1, min(pw - 1, lx2 + 3) : min(pw, lx2 + 8)],
                patch[ly1 : ly2 + 1, max(0, lx2 - 7) : max(1, lx2 - 2)],
            ),
            (
                0,
                patch[max(0, ly2 - 2) : min(ph, ly2 + 3), lx1 : lx2 + 1],
                patch[min(ph - 1, ly2 + 3) : min(ph, ly2 + 8), lx1 : lx2 + 1],
                patch[max(0, ly2 - 7) : max(1, ly2 - 2), lx1 : lx2 + 1],
            ),
            (
                1,
                patch[ly1 : ly2 + 1, max(0, lx1 - 2) : min(pw, lx1 + 3)],
                patch[ly1 : ly2 + 1, max(0, lx1 - 7) : max(1, lx1 - 2)],
                patch[ly1 : ly2 + 1, min(pw - 1, lx1 + 3) : min(pw, lx1 + 8)],
            ),
        )
        covs: list[float] = []
        for axis, band, out_band, in_band in edges:
            if band.size == 0:
                covs.append(0.0)
                continue
            out_med = float(np.median(out_band)) if out_band.size else 254.0
            in_med = float(np.median(in_band)) if in_band.size else 254.0
            bg = max(out_med, in_med)
            strip_min = np.min(band, axis=axis)
            cov = float(np.count_nonzero((bg - strip_min) >= 4.0)) / float(max(1, strip_min.size))
            covs.append(cov)
        return covs[0], covs[1], covs[2], covs[3]

    @staticmethod
    def _pick_standard_target_aspect_ratio(
        anchor_px: float,
        other_px: float,
        *,
        anchor_is_long: bool,
        effective_dpi: float,
        box_area_ratio: float = 0.0,
    ) -> float | None:
        """Select a target `long / short` aspect ratio when `other_px` appears under-detected."""
        if anchor_px <= 0.0 or other_px <= 0.0:
            return None
        cur_ratio = (anchor_px / other_px) if anchor_is_long else (other_px / anchor_px)
        # Do not force elongated receipts/slips (ratio > 1.92) or near-square items (< 1.10).
        if cur_ratio > 1.92 or cur_ratio < 1.10:
            return None

        anchor_mm = anchor_px * MM_PER_INCH / effective_dpi if effective_dpi > 0.0 else 0.0
        if effective_dpi > 0.0:
            other_mm = other_px * MM_PER_INCH / effective_dpi
            best_ratio: float | None = None
            best_err = 999.0
            for short_mm, long_mm, _ in STANDARD_PAPER_SPECS_MM:
                ref_anchor = long_mm if anchor_is_long else short_mm
                ref_other = short_mm if anchor_is_long else long_mm
                rel_err = abs(anchor_mm - ref_anchor) / ref_anchor
                if (
                    rel_err <= 0.065
                    and ref_other * 0.72 <= other_mm <= ref_other * 0.985
                    and rel_err < best_err
                ):
                    best_err = rel_err
                    best_ratio = long_mm / short_mm
            if best_ratio is not None:
                return best_ratio

        # Resolution-independent postcard fallback (`100:148 = 1.480`): restrict strictly to
        # smaller documents (occupying < 28% of platen area and under 175mm when DPI is known)
        # so large sheets like A4 on an A3 platen are never stretched to postcard proportions.
        is_small_sheet = box_area_ratio < 0.28 and (effective_dpi <= 0.0 or anchor_mm < 175.0)
        if is_small_sheet:
            if anchor_is_long and 1.50 <= cur_ratio <= 1.88:
                return 1.480
            if (not anchor_is_long) and 1.15 <= cur_ratio <= 1.39:
                return 1.480
        return None

    @classmethod
    def _complete_margins_by_standard_ratio(
        cls,
        *,
        patch: np.ndarray,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        pad_t: float,
        pad_r: float,
        pad_b: float,
        pad_l: float,
        horiz_supported: bool,
        vert_supported: bool,
        box_confidence: float,
        bg_fill: int,
        effective_dpi: float,
        work_area: float,
        local_point_valid: Callable[[float, float], bool],
    ) -> tuple[float, float, float, float]:
        """Extend unanchored edge(s) outward to match standard paper proportions."""
        lx1 = int(round(x1 - pad_l))
        lx2 = int(round(x2 + pad_r))
        ly1 = int(round(y1 - pad_t))
        ly2 = int(round(y2 + pad_b))
        cur_w = float(lx2 - lx1)
        cur_h = float(ly2 - ly1)
        if cur_w < 16.0 or cur_h < 16.0:
            return pad_t, pad_r, pad_b, pad_l

        cov_t, cov_r, cov_b, cov_l = cls._measure_patch_edge_coverage(patch, lx1, ly1, lx2, ly2)

        # Skip when all 4 edges (or 3+ edges of a high-rectangularity bordered card) are solid.
        if min(cov_t, cov_b) >= 0.45 and min(cov_l, cov_r) >= 0.45:
            return pad_t, pad_r, pad_b, pad_l
        if (
            box_confidence >= 0.90
            and sum(1 for c in (cov_t, cov_r, cov_b, cov_l) if c >= 0.65) >= 3
        ):
            return pad_t, pad_r, pad_b, pad_l

        w_anchored = (cov_l >= 0.45 and cov_r >= 0.45) or (
            max(cov_t, cov_b) >= 0.65 and horiz_supported
        )
        h_anchored = (cov_t >= 0.45 and cov_b >= 0.45) or (
            max(cov_l, cov_r) >= 0.65 and vert_supported
        )

        # Decide which axis acts as anchor and which axis needs unanchored edge completion.
        expand_vertical: bool | None = None
        if w_anchored and min(cov_t, cov_b) < 0.35:
            if not h_anchored or cur_w >= cur_h:
                expand_vertical = True
        if expand_vertical is None and h_anchored and min(cov_l, cov_r) < 0.35:
            if not w_anchored or cur_h > cur_w:
                expand_vertical = False
        if expand_vertical is None:
            return pad_t, pad_r, pad_b, pad_l

        if expand_vertical:
            anchor_px = cur_w
            other_px = cur_h
            cov_neg, cov_pos = cov_t, cov_b
        else:
            anchor_px = cur_h
            other_px = cur_w
            cov_neg, cov_pos = cov_l, cov_r

        anchor_is_long = anchor_px >= other_px
        box_area_ratio = (cur_w * cur_h) / work_area if work_area > 0.0 else 0.0
        target_ratio = cls._pick_standard_target_aspect_ratio(
            anchor_px,
            other_px,
            anchor_is_long=anchor_is_long,
            effective_dpi=effective_dpi,
            box_area_ratio=box_area_ratio,
        )
        if target_ratio is None:
            return pad_t, pad_r, pad_b, pad_l

        target_other = (anchor_px / target_ratio) if anchor_is_long else (anchor_px * target_ratio)
        deficit = target_other - other_px
        if deficit <= 1.0 or deficit > other_px * 0.28:
            return pad_t, pad_r, pad_b, pad_l

        # Keep anchored side(s) fixed and allocate missing expansion onto unanchored side(s).
        if cov_neg >= 0.45 and cov_pos < 0.35:
            neg_req, pos_req = 0.0, deficit
        elif cov_pos >= 0.45 and cov_neg < 0.35:
            neg_req, pos_req = deficit, 0.0
        elif cov_neg < 0.35 and cov_pos < 0.35:
            neg_req, pos_req = deficit * 0.5, deficit * 0.5
        else:
            return pad_t, pad_r, pad_b, pad_l

        ph, pw = patch.shape[:2]
        white_floor = float(bg_fill) - 18.0
        dark_p10_floor = float(bg_fill) - 48.0

        def safe_extend(is_vertical: bool, direction: int, req_px: float) -> float:
            steps = int(round(req_px))
            if steps <= 0:
                return 0.0
            gained = 0
            span_a = max(0, lx1 if is_vertical else ly1)
            span_b = min((pw - 1) if is_vertical else (ph - 1), lx2 if is_vertical else ly2)
            if is_vertical:
                start_pos = ly1 if direction < 0 else ly2
            else:
                start_pos = lx1 if direction < 0 else lx2
            mid_coord = (span_a + span_b) * 0.5
            for d in range(1, steps + 1):
                pos = start_pos + direction * d
                if pos < 2 or pos >= ((ph - 2) if is_vertical else (pw - 2)):
                    break
                if is_vertical:
                    if not local_point_valid(mid_coord, float(pos)):
                        break
                    strip = patch[pos, span_a : span_b + 1]
                else:
                    if not local_point_valid(float(pos), mid_coord):
                        break
                    strip = patch[span_a : span_b + 1, pos]
                if strip.size == 0:
                    break
                if (
                    float(np.median(strip)) < white_floor
                    or float(np.percentile(strip, 10)) < dark_p10_floor
                ):
                    break
                gained = d
            return float(gained)

        if expand_vertical:
            pad_t += safe_extend(True, -1, neg_req)
            pad_b += safe_extend(True, 1, pos_req)
        else:
            pad_l += safe_extend(False, -1, neg_req)
            pad_r += safe_extend(False, 1, pos_req)

        return pad_t, pad_r, pad_b, pad_l

    @staticmethod
    def _find_paper_to_platen_step(
        profile: np.ndarray, *, sensitivity: int = 0
    ) -> float | None:
        """Detect distance from a printed boundary outward to the physical paper-to-platen step.

        Works for single/standard white sheets (e.g. A4 placed on an A3 white platen cover)
        where the sheet's blank white margin (~248-252.8 luminance) transitions to a flat,
        saturated white platen cover (~253.5-255.0 with near-zero variance), either with or
        without a dark paper-edge shadow valley.
        """
        n = int(profile.size)
        if n < 18:
            return None

        # Outer platen reference: inspect the outer 35% of the valid profile.
        outer_start = max(8, int(n * 0.65))
        outer_tail = profile[outer_start:]
        if outer_tail.size < 6:
            return None

        platen_level = float(np.median(outer_tail))
        q75, q25 = np.percentile(outer_tail, [75, 25])
        outer_iqr = float(q75 - q25)
        platen_floor = max(246.0, 252.0 - max(0, sensitivity) * 1.0)
        # Require a genuinely uniform, saturated bright white platen region outside the sheet.
        if platen_level < platen_floor or outer_iqr > (1.2 + max(0, sensitivity) * 0.15):
            return None

        if sensitivity >= 0:
            valley_out_min = max(0.5, 1.5 - sensitivity * 0.2)
            valley_in_min = max(0.35, 1.0 - sensitivity * 0.13)
        else:
            valley_out_min = 1.5 - sensitivity * 0.5
            valley_in_min = 1.0 - sensitivity * 0.35

        # 1) Check for a distinct paper-edge shadow valley whose outer side opens directly
        #    onto the uniform saturated platen cover.
        best_valley_idx: int | None = None
        best_valley_depth = 0.0
        for idx in range(2, n - 5):
            val = float(profile[idx])
            in_slice = profile[max(0, idx - 8) : idx]
            if in_slice.size == 0:
                continue
            in_med = float(np.median(in_slice))
            out_med = float(np.median(profile[idx + 2 : min(n, idx + 12)]))
            tail_check = profile[idx + 3 : min(n, idx + 45)]
            if tail_check.size == 0 or float(np.min(tail_check)) < (platen_level - 1.5):
                continue
            if (
                abs(out_med - platen_level) <= 0.85
                and (out_med - val) >= valley_out_min
                and (in_med - val) >= valley_in_min
            ):
                depth = min(out_med - val, in_med - val)
                if depth > best_valley_depth:
                    best_valley_depth = depth
                    best_valley_idx = idx
        if best_valley_idx is not None:
            return float(best_valley_idx)

        # 2) Otherwise detect a clean brightness step from the inner paper margin up to
        #    the flat saturated platen level.
        inner_end = min(16, max(6, n // 2))
        inner_paper_slice = profile[2:inner_end]
        if inner_paper_slice.size < 4:
            return None
        paper_level = float(np.median(inner_paper_slice))
        step_height = platen_level - paper_level
        base_step = 0.65 if outer_iqr <= 0.8 else 1.2
        min_step = (
            max(0.25, base_step - sensitivity * 0.1)
            if sensitivity >= 0
            else (base_step - sensitivity * 0.25)
        )
        if step_height < min_step:
            return None

        thresh = paper_level + step_height * 0.65
        for idx in range(2, n - 5):
            window_out = profile[idx + 1 : min(n, idx + 10)]
            if (
                window_out.size >= 5
                and float(np.median(window_out)) >= thresh
                and float(np.min(window_out)) >= (platen_level - 0.85)
            ):
                return float(idx)
        return None

    @classmethod
    def _detect_standard_sheet_edge_offsets(
        cls,
        work_gray: np.ndarray,
        box: RotatedCropBox,
        *,
        solid_boxes: Sequence[RotatedCropBox] = (),
        sensitivity: int = 0,
    ) -> tuple[float | None, float | None, float | None, float | None]:
        """Return `(off_top, off_right, off_bottom, off_left)` to paper-to-platen boundaries."""
        h, w = work_gray.shape[:2]
        if box.width < 16.0 or box.height < 16.0:
            return None, None, None, None

        pad_normal = max(120, int(round(max(h, w) * 0.30)))
        patch_w = int(round(box.width)) + pad_normal * 2
        patch_h = int(round(box.height)) + pad_normal * 2

        rot_mat = cv2.getRotationMatrix2D((box.center_x, box.center_y), box.angle_deg, 1.0)
        rot_mat[0, 2] += (patch_w * 0.5) - box.center_x
        rot_mat[1, 2] += (patch_h * 0.5) - box.center_y
        inv_mat = cv2.invertAffineTransform(rot_mat)

        bg_fill = int(round(float(np.percentile(work_gray, 95))))
        patch = cv2.warpAffine(
            work_gray,
            rot_mat,
            (patch_w, patch_h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=bg_fill,
        ).astype(np.float32)

        x1 = int(round(pad_normal))
        y1 = int(round(pad_normal))
        x2 = int(round(pad_normal + box.width))
        y2 = int(round(pad_normal + box.height))

        solid_rects = [b.bounding_rect() for b in solid_boxes]
        border_inset = 6.0
        collision_margin = 6.0

        def valid_extent(start_x: float, start_y: float, dx: float, dy: float, max_d: int) -> int:
            for d in range(1, max_d + 1):
                lx = start_x + dx * d
                ly = start_y + dy * d
                src_x = inv_mat[0, 0] * lx + inv_mat[0, 1] * ly + inv_mat[0, 2]
                src_y = inv_mat[1, 0] * lx + inv_mat[1, 1] * ly + inv_mat[1, 2]
                if (
                    src_x < border_inset
                    or src_x > (w - 1.0 - border_inset)
                    or src_y < border_inset
                    or src_y > (h - 1.0 - border_inset)
                ):
                    return d
                for ox, oy, ow, oh in solid_rects:
                    if (
                        (ox - collision_margin) <= src_x <= (ox + ow + collision_margin)
                        and (oy - collision_margin) <= src_y <= (oy + oh + collision_margin)
                    ):
                        return d
            return max_d

        mid_x = (x1 + x2) * 0.5
        mid_y = (y1 + y2) * 0.5

        ext_t = valid_extent(mid_x, float(y1), 0.0, -1.0, min(pad_normal, y1))
        ext_b = valid_extent(mid_x, float(y2), 0.0, 1.0, min(pad_normal, patch_h - 1 - y2))
        ext_l = valid_extent(float(x1), mid_y, -1.0, 0.0, min(pad_normal, x1))
        ext_r = valid_extent(float(x2), mid_y, 1.0, 0.0, min(pad_normal, patch_w - 1 - x2))

        off_t = cls._detect_segmented_paper_step(
            patch[y1 - ext_t : y1 + 1, x1 : x2 + 1],
            axis=1,
            reverse=True,
            sensitivity=sensitivity,
        )
        off_b = cls._detect_segmented_paper_step(
            patch[y2 : y2 + ext_b + 1, x1 : x2 + 1],
            axis=1,
            reverse=False,
            sensitivity=sensitivity,
        )
        off_l = cls._detect_segmented_paper_step(
            patch[y1 : y2 + 1, x1 - ext_l : x1 + 1],
            axis=0,
            reverse=True,
            sensitivity=sensitivity,
        )
        off_r = cls._detect_segmented_paper_step(
            patch[y1 : y2 + 1, x2 : x2 + ext_r + 1],
            axis=0,
            reverse=False,
            sensitivity=sensitivity,
        )
        return off_t, off_r, off_b, off_l

    @classmethod
    def _detect_segmented_paper_step(
        cls,
        strip: np.ndarray,
        *,
        axis: int,
        reverse: bool,
        sensitivity: int = 0,
    ) -> float | None:
        """Detect paper-to-platen step across edge sub-segments to tolerate slight skew."""
        if strip.size == 0:
            return None
        whole_prof = np.mean(strip, axis=axis)
        if reverse:
            whole_prof = whole_prof[::-1]
        whole_off = cls._find_paper_to_platen_step(whole_prof, sensitivity=sensitivity)

        span = strip.shape[1] if axis == 1 else strip.shape[0]
        num_segs = max(1, min(4, span // 80))
        if num_segs <= 1:
            return whole_off

        seg_offs: list[float] = []
        for idx in range(num_segs):
            start = (span * idx) // num_segs
            end = (span * (idx + 1)) // num_segs
            if end - start < 24:
                continue
            sub_strip = strip[:, start:end] if axis == 1 else strip[start:end, :]
            prof = np.mean(sub_strip, axis=axis)
            if reverse:
                prof = prof[::-1]
            off = cls._find_paper_to_platen_step(prof, sensitivity=sensitivity)
            if off is not None:
                seg_offs.append(off)

        if len(seg_offs) >= 2:
            return float(np.median(seg_offs))
        if whole_off is not None:
            return whole_off
        return seg_offs[0] if seg_offs else None

    def _should_auto_select_standard_sheet(
        self,
        work_bgr: np.ndarray,
        raw_box: RotatedCropBox,
    ) -> bool:
        """Return True when `raw_box` is a single standard sheet (e.g. A4) on a larger platen."""
        h, w = work_bgr.shape[:2]
        w_ratio = raw_box.width / float(w)
        h_ratio = raw_box.height / float(h)
        area_ratio = w_ratio * h_ratio
        if w_ratio < 0.35 or h_ratio < 0.35 or area_ratio < 0.18:
            return False

        work_gray = cv2.cvtColor(work_bgr, cv2.COLOR_BGR2GRAY)
        offsets = self._detect_standard_sheet_edge_offsets(
            work_gray, raw_box, sensitivity=self._clamped_sensitivity()
        )
        detected_sides = sum(1 for off in offsets if off is not None)
        return detected_sides >= 2

    def _attach_nearby_subblocks(
        self,
        base_blocks: Sequence[RotatedCropBox],
        subblocks: Sequence[RotatedCropBox],
        *,
        work_w: int,
        work_h: int,
    ) -> list[RotatedCropBox]:
        """Attach nearby interior text/logo sub-blocks aligned with `base_blocks`."""
        if not base_blocks or not subblocks:
            return list(base_blocks)

        dom = max(base_blocks, key=lambda b: b.area)
        dom_angle = dom.angle_deg
        rad = math.radians(dom_angle)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)

        def project(box: RotatedCropBox) -> tuple[float, float, float, float]:
            pts = box.corners()
            u = pts[:, 0] * cos_a + pts[:, 1] * sin_a
            v = -pts[:, 0] * sin_a + pts[:, 1] * cos_a
            return float(np.min(u)), float(np.max(u)), float(np.min(v)), float(np.max(v))

        base_union = self._enclose_boxes_at_angle(
            base_blocks,
            angle_deg=dom_angle,
            work_w=work_w,
            work_h=work_h,
        )
        bu_min, bu_max, bv_min, bv_max = project(base_union)
        max_dim = max(base_union.width, base_union.height)
        tol_short = max(24.0, max_dim * 0.045)
        tol_long = max(36.0, max_dim * 0.14)

        attached = list(base_blocks)
        cur_u_min, cur_u_max, cur_v_min, cur_v_max = bu_min, bu_max, bv_min, bv_max
        remaining = list(subblocks)
        # Iterate briefly so stacked header elements (body -> title -> logo/code) chain together.
        for _ in range(3):
            next_remaining: list[RotatedCropBox] = []
            progressed = False
            for sb in remaining:
                su_min, su_max, sv_min, sv_max = project(sb)
                if (
                    su_min >= bu_min - 2.0
                    and su_max <= bu_max + 2.0
                    and sv_min >= bv_min - 2.0
                    and sv_max <= bv_max + 2.0
                ):
                    continue
                gap_u = max(0.0, cur_u_min - su_max, su_min - cur_u_max)
                gap_v = max(0.0, cur_v_min - sv_max, sv_min - cur_v_max)
                fits_u_span = su_min >= bu_min - tol_short and su_max <= bu_max + tol_short
                fits_v_span = sv_min >= bv_min - tol_short and sv_max <= bv_max + tol_short
                extend_v = fits_u_span and gap_u <= tol_short and gap_v <= tol_long
                extend_u = fits_v_span and gap_v <= tol_short and gap_u <= tol_long
                if extend_v or extend_u:
                    attached.append(sb)
                    cur_u_min = min(cur_u_min, su_min)
                    cur_u_max = max(cur_u_max, su_max)
                    cur_v_min = min(cur_v_min, sv_min)
                    cur_v_max = max(cur_v_max, sv_max)
                    progressed = True
                else:
                    next_remaining.append(sb)
            remaining = next_remaining
            if not progressed or not remaining:
                break
        return attached

    def _find_auto_standard_sheet_group(
        self,
        work_bgr: np.ndarray,
        merged_pairs: list[tuple[RotatedCropBox, RotatedCropBox]],
        solid_work_boxes: list[RotatedCropBox],
        *,
        subblocks: Sequence[RotatedCropBox] = (),
    ) -> tuple[RotatedCropBox, RotatedCropBox] | None:
        """Return unified `(raw, expanded)` pair when blocks belong to one standard white sheet."""
        work_h, work_w = work_bgr.shape[:2]
        base_blocks: list[RotatedCropBox] = [
            rb for rb, _ in merged_pairs if min(rb.width, rb.height) >= 14.0
        ] + [sb for sb in solid_work_boxes if min(sb.width, sb.height) >= 14.0]

        if not solid_work_boxes and len(merged_pairs) == 1:
            pair = merged_pairs[0]
            attached = self._attach_nearby_subblocks(
                [pair[0]], subblocks, work_w=work_w, work_h=work_h
            )
            if len(attached) == 1:
                return pair if self._should_auto_select_standard_sheet(work_bgr, pair[0]) else None
            raw_blocks = attached
        else:
            if len(base_blocks) < 2:
                return None
            angles = [b.angle_deg for b in base_blocks]
            if (max(angles) - min(angles)) > 4.5:
                return None
            raw_blocks = self._attach_nearby_subblocks(
                base_blocks, subblocks, work_w=work_w, work_h=work_h
            )

        if len(raw_blocks) < 2:
            return None

        base_angles = [b.angle_deg for b in (base_blocks if base_blocks else raw_blocks)]
        if (max(base_angles) - min(base_angles)) > 4.5:
            return None

        dom = max(raw_blocks, key=lambda b: b.area)
        unified_raw = self._enclose_boxes_at_angle(
            raw_blocks,
            angle_deg=dom.angle_deg,
            work_w=work_w,
            work_h=work_h,
        )
        w_ratio = unified_raw.width / float(work_w)
        h_ratio = unified_raw.height / float(work_h)
        area_ratio = w_ratio * h_ratio
        if w_ratio < 0.35 or h_ratio < 0.35 or area_ratio < 0.22:
            return None

        # Ensure all blocks sit within a single document column (or closely-spaced horizontal
        # row, NOT side-by-side separate receipts) and match normal sheet proportions.
        max_block_w = max(b.width for b in raw_blocks)
        max_block_h = max(b.height for b in raw_blocks)
        same_col = unified_raw.width <= max_block_w * 1.28
        same_row = unified_raw.height <= max_block_h * 1.28
        if same_row and not same_col:
            # Reject side-by-side distinct sheets/receipts separated by wide horizontal gaps.
            sorted_x = sorted(
                (b.bounding_rect()[0], b.bounding_rect()[0] + b.bounding_rect()[2])
                for b in raw_blocks
            )
            max_gap_x = max(
                (sorted_x[idx + 1][0] - sorted_x[idx][1]) for idx in range(len(sorted_x) - 1)
            )
            if max_gap_x > max(work_w, work_h) * 0.04 or len(solid_work_boxes) >= 2:
                same_row = False
        if not (same_col or same_row):
            return None

        union_aspect = max(unified_raw.width, unified_raw.height) / max(
            1.0, min(unified_raw.width, unified_raw.height)
        )
        if union_aspect > 1.65:
            return None

        # Either 2+ platen-step sides are visible around the unified sheet, OR the aligned blocks
        # collectively form one large-format letter/notice (>= 38% width, >= 55% height, >= 25%
        # area, aspect <= 1.55) without being separate 4-sided bordered cards.
        has_step_sides = self._should_auto_select_standard_sheet(work_bgr, unified_raw)
        is_dominant_letter_layout = (
            w_ratio >= 0.38
            and h_ratio >= 0.55
            and area_ratio >= 0.25
            and union_aspect <= 1.55
            and (max_block_w / float(work_w)) >= 0.36
            and len(solid_work_boxes) <= 2
        )
        if not (has_step_sides or is_dominant_letter_layout):
            return None

        work_gray = cv2.cvtColor(work_bgr, cv2.COLOR_BGR2GRAY)
        unified_exp = self._expand_printed_group_to_paper_edges(
            work_gray,
            unified_raw,
            solid_boxes=[],
            aspect_ratio_completion=False,
        )
        return unified_raw, unified_exp

    def _build_standard_sheet_candidates(
        self,
        work_bgr: np.ndarray,
        merged_pairs: list[tuple[RotatedCropBox, RotatedCropBox]],
        solid_work_boxes: list[RotatedCropBox],
        *,
        inv_scale: float,
        orig_w: int,
        orig_h: int,
        dpi: float,
        scale: float = 1.0,
        unify_single_sheet: bool,
        subblocks: Sequence[RotatedCropBox] = (),
    ) -> list[RotatedCropBox]:
        """Expand printed groups to physical paper-to-platen boundaries for standard sheets."""
        if not merged_pairs and not solid_work_boxes:
            return []

        work_h, work_w = work_bgr.shape[:2]
        work_gray = cv2.cvtColor(work_bgr, cv2.COLOR_BGR2GRAY)

        target_pairs = list(merged_pairs)
        if unify_single_sheet:
            base_blocks = [
                rb for rb, _ in target_pairs if min(rb.width, rb.height) >= 14.0
            ] + [sb for sb in solid_work_boxes if min(sb.width, sb.height) >= 14.0]
            all_blocks = self._attach_nearby_subblocks(
                base_blocks, subblocks, work_w=work_w, work_h=work_h
            )
            if len(all_blocks) > 1:
                dom_idx = max(range(len(all_blocks)), key=lambda k: all_blocks[k].area)
                dom_angle = all_blocks[dom_idx].angle_deg
                unified_raw = self._enclose_boxes_at_angle(
                    all_blocks,
                    angle_deg=dom_angle,
                    work_w=work_w,
                    work_h=work_h,
                )
                unified_exp = self._expand_printed_group_to_paper_edges(
                    work_gray,
                    unified_raw,
                    solid_boxes=[],
                    dpi=dpi,
                    scale=scale,
                    aspect_ratio_completion=False,
                )
                target_pairs = [(unified_raw, unified_exp)]

        results: list[RotatedCropBox] = []
        sensitivity = self._clamped_sensitivity()
        for raw_wb, fallback_exp_wb in target_pairs:
            off_t, off_r, off_b, off_l = self._detect_standard_sheet_edge_offsets(
                work_gray, raw_wb, solid_boxes=solid_work_boxes, sensitivity=sensitivity
            )
            # Project fallback_exp_wb into raw_wb's local frame for fallback per-side paddings.
            rad = math.radians(raw_wb.angle_deg)
            cos_a = math.cos(rad)
            sin_a = math.sin(rad)
            raw_pts = raw_wb.corners()
            fb_pts = fallback_exp_wb.corners()
            raw_u = raw_pts[:, 0] * cos_a + raw_pts[:, 1] * sin_a
            raw_v = -raw_pts[:, 0] * sin_a + raw_pts[:, 1] * cos_a
            fb_u = fb_pts[:, 0] * cos_a + fb_pts[:, 1] * sin_a
            fb_v = -fb_pts[:, 0] * sin_a + fb_pts[:, 1] * cos_a

            fb_l = max(0.0, float(np.min(raw_u) - np.min(fb_u)))
            fb_r = max(0.0, float(np.max(fb_u) - np.max(raw_u)))
            fb_t = max(0.0, float(np.min(raw_v) - np.min(fb_v)))
            fb_b = max(0.0, float(np.max(fb_v) - np.max(raw_v)))

            pad_t = off_t if off_t is not None else fb_t
            pad_r = off_r if off_r is not None else fb_r
            pad_b = off_b if off_b is not None else fb_b
            pad_l = off_l if off_l is not None else fb_l

            # If a large A4-class sheet still falls short of physical A4 size (210x297mm) on
            # unanchored sides because paper-to-platen contrast is too faint, pad missing sides
            # outward toward A4 dimensions.
            a4_completed = False
            if dpi > 0.0 and scale > 0.0:
                px_per_mm = (float(dpi) * float(scale)) / 25.4
                raw_short_mm = min(raw_wb.width, raw_wb.height) / px_per_mm
                raw_long_mm = max(raw_wb.width, raw_wb.height) / px_per_mm
                if 155.0 <= raw_short_mm <= 212.0 and 212.0 <= raw_long_mm <= 300.0:
                    is_portrait = raw_wb.height >= raw_wb.width
                    target_w_px = (210.0 if is_portrait else 297.0) * px_per_mm
                    target_h_px = (297.0 if is_portrait else 210.0) * px_per_mm
                    max_pad_px = 38.0 * px_per_mm

                    cur_w = raw_wb.width + pad_l + pad_r
                    if cur_w < target_w_px * 0.96 and (off_l is None or off_r is None):
                        deficit_w = target_w_px - cur_w
                        if off_l is None and off_r is None:
                            pad_l = min(max_pad_px, pad_l + deficit_w * 0.5)
                            pad_r = min(max_pad_px, pad_r + deficit_w * 0.5)
                        elif off_l is None:
                            pad_l = min(max_pad_px, pad_l + deficit_w)
                        else:
                            pad_r = min(max_pad_px, pad_r + deficit_w)
                        a4_completed = True

                    cur_h = raw_wb.height + pad_t + pad_b
                    if cur_h < target_h_px * 0.96 and (off_t is None or off_b is None):
                        deficit_h = target_h_px - cur_h
                        if off_t is None and off_b is None:
                            pad_t = min(max_pad_px, pad_t + deficit_h * 0.5)
                            pad_b = min(max_pad_px, pad_b + deficit_h * 0.5)
                        elif off_t is None:
                            pad_t = min(max_pad_px, pad_t + deficit_h)
                        else:
                            pad_b = min(max_pad_px, pad_b + deficit_h)
                        a4_completed = True

            if all(off is None for off in (off_t, off_r, off_b, off_l)) and not a4_completed:
                chosen_wb = fallback_exp_wb
            else:
                bx, by, bw, bh = raw_wb.bounding_rect()
                pad_l = min(pad_l, max(0.0, bx))
                pad_r = min(pad_r, max(0.0, float(work_w - 1) - (bx + bw)))
                pad_t = min(pad_t, max(0.0, by))
                pad_b = min(pad_b, max(0.0, float(work_h - 1) - (by + bh)))

                eff_cx, eff_cy, eff_w, eff_h, eff_angle = raw_wb.with_edge_margins(
                    top=pad_t, right=pad_r, bottom=pad_b, left=pad_l, unit="px"
                ).resolved_geometry()
                chosen_wb = RotatedCropBox(
                    center_x=eff_cx,
                    center_y=eff_cy,
                    width=min(float(work_w), eff_w),
                    height=min(float(work_h), eff_h),
                    angle_deg=eff_angle,
                    confidence=raw_wb.confidence,
                    source_width=work_w,
                    source_height=work_h,
                )

            results.append(
                RotatedCropBox(
                    center_x=chosen_wb.center_x * inv_scale,
                    center_y=chosen_wb.center_y * inv_scale,
                    width=chosen_wb.width * inv_scale,
                    height=chosen_wb.height * inv_scale,
                    angle_deg=chosen_wb.angle_deg,
                    confidence=chosen_wb.confidence,
                    index=0,
                    source_width=orig_w,
                    source_height=orig_h,
                    source_dpi=float(dpi),
                )
            )
        return results

    def _is_printed_content_on_full_page(
        self,
        work_bgr: np.ndarray,
        boxes: list[RotatedCropBox],
        scale: float,
    ) -> bool:
        """Return True when all detected boxes are printed regions inside one full-page white sheet.

        Prevents shrinking an A4 (or other selected full-page paper size) scan inward onto
        internal tables or text blocks when the paper's white margins blend into the scan
        frame border with no distinct outer paper-edge boundary.
        """
        if not boxes:
            return False

        h, w = work_bgr.shape[:2]
        lab = cv2.cvtColor(work_bgr, cv2.COLOR_BGR2LAB)
        bg_lab, _ = self._estimate_background_stats(lab)

        # Only applies when image borders look like bright neutral white/off-white paper.
        bg_l = float(bg_lab[0])
        bg_chroma = math.hypot(float(bg_lab[1]) - 128.0, float(bg_lab[2]) - 128.0)
        if bg_l < 185.0 or bg_chroma > 24.0:
            return False

        # Require the union of detected printed boxes to span a substantial portion of the scan
        # frame so a single small white receipt on a larger A3/A4 platen is NOT expanded to full.
        # Also reject full-page fallback when detected boxes are tilted or mutually misaligned
        # (e.g. two skewed postcards placed separately on an A3 platen).
        if any(abs(b.angle_deg) > 4.5 for b in boxes):
            return False
        total_box_area_ratio = sum(b.area * (float(scale) ** 2) for b in boxes) / float(h * w)
        if total_box_area_ratio < 0.24:
            return False

        all_corners = np.vstack([b.corners() * float(scale) for b in boxes])
        ux1 = float(np.min(all_corners[:, 0]))
        uy1 = float(np.min(all_corners[:, 1]))
        ux2 = float(np.max(all_corners[:, 0]))
        uy2 = float(np.max(all_corners[:, 1]))
        union_w_ratio = (ux2 - ux1) / float(w)
        union_h_ratio = (uy2 - uy1) / float(h)
        union_area_ratio = union_w_ratio * union_h_ratio
        if union_w_ratio < 0.50 or union_h_ratio < 0.50 or union_area_ratio < 0.30:
            return False

        # If a smaller standard sheet (such as an A4 document placed on an A3 platen) has a
        # distinct paper-to-platen brightness step or paper-edge shadow on 2 or more sides
        # inside a saturated white platen border, it is NOT a full-page scan.
        work_gray = cv2.cvtColor(work_bgr, cv2.COLOR_BGR2GRAY)
        union_work_box = RotatedCropBox(
            center_x=(ux1 + ux2) * 0.5,
            center_y=(uy1 + uy2) * 0.5,
            width=max(1.0, ux2 - ux1),
            height=max(1.0, uy2 - uy1),
            angle_deg=boxes[0].angle_deg if len(boxes) == 1 else 0.0,
            source_width=w,
            source_height=h,
        )
        step_offsets = self._detect_standard_sheet_edge_offsets(work_gray, union_work_box)
        if sum(1 for off in step_offsets if off is not None) >= 2:
            return False

        rim_px = max(4, min(12, int(round(min(h, w) * 0.01))))
        rim_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (rim_px * 2 + 1, rim_px * 2 + 1))

        for box in boxes:
            pts = box.corners() * float(scale)
            poly = np.round(pts).astype(np.int32)
            box_mask = np.zeros((h, w), dtype=np.uint8)
            cv2.fillConvexPoly(box_mask, poly, 255)
            inside_pixels = lab[box_mask > 0].astype(np.float32)
            if len(inside_pixels) < 50:
                return False

            # Check if the box boundary sits directly on dark printed ink (text/table rules)
            # rather than on a physical paper edge surrounded by blank paper margins.
            eroded = cv2.erode(box_mask, rim_kernel)
            inner_rim = cv2.subtract(box_mask, eroded)
            rim_l = lab[:, :, 0][inner_rim > 0].astype(np.float32)
            if rim_l.size == 0:
                return False

            dark_ink_mask = (bg_l - rim_l) >= self.config.printed_border_ink_drop_l
            ink_ratio = float(np.count_nonzero(dark_ink_mask)) / float(rim_l.size)
            if ink_ratio < 0.03:
                return False

            # Estimate the unprinted surface color inside the detected box using its
            # brightest luminance tail (excluding dark ink strokes).
            l_cutoff = float(np.percentile(inside_pixels[:, 0], 88))
            bright_inside = inside_pixels[inside_pixels[:, 0] >= l_cutoff]
            if len(bright_inside) == 0:
                return False
            paper_lab = np.median(bright_inside, axis=0)
            paper_l = float(paper_lab[0])
            paper_chroma = math.hypot(
                float(paper_lab[1]) - 128.0, float(paper_lab[2]) - 128.0
            )
            delta_e_paper = float(
                math.sqrt(
                    ((paper_l - bg_l) * 1.15) ** 2
                    + (float(paper_lab[1]) - float(bg_lab[1])) ** 2
                    + (float(paper_lab[2]) - float(bg_lab[2])) ** 2
                )
            )
            if delta_e_paper <= self.config.paper_color_match_max_delta_e:
                continue

            # Also handle forms whose entire inner table is shaded light neutral gray
            # with a dark printed border sitting near-axis-aligned inside the full page.
            width_ratio = (box.width * float(scale)) / float(w)
            height_ratio = (box.height * float(scale)) / float(h)
            is_shaded_neutral_table = (
                paper_l >= 180.0
                and paper_chroma <= 15.0
                and abs(box.angle_deg) <= 5.0
                and width_ratio >= 0.50
                and height_ratio >= 0.50
                and ink_ratio >= 0.08
            )
            if not is_shaded_neutral_table:
                return False

        return True

    def _has_full_page_printed_ink(self, work_bgr: np.ndarray) -> bool:
        """Detect text-only full-page white documents whose thin strokes form no large contour."""
        h, w = work_bgr.shape[:2]
        if min(h, w) < 40:
            return False

        lab = cv2.cvtColor(work_bgr, cv2.COLOR_BGR2LAB)
        bg_lab, bg_sigma = self._estimate_background_stats(lab)
        bg_l = float(bg_lab[0])
        bg_chroma = math.hypot(float(bg_lab[1]) - 128.0, float(bg_lab[2]) - 128.0)
        if bg_l < 185.0 or bg_chroma > 24.0:
            return False

        mx = max(4, int(round(w * 0.03)))
        my = max(4, int(round(h * 0.03)))
        interior_l = lab[my : h - my, mx : w - mx, 0].astype(np.float32)
        if interior_l.size == 0:
            return False

        ink_drop = max(self.config.printed_border_ink_drop_l, bg_sigma * 6.0 + 25.0)
        ink_mask = (bg_l - interior_l) >= ink_drop
        ink_ratio = float(np.count_nonzero(ink_mask)) / float(interior_l.size)
        if ink_ratio < 0.0025:
            return False

        # Ensure the ink spans a broad portion of the page rather than one tiny corner slip.
        ys, xs = np.nonzero(ink_mask)
        if ys.size < 20:
            return False
        span_x = float(np.percentile(xs, 95) - np.percentile(xs, 5)) / float(interior_l.shape[1])
        span_y = float(np.percentile(ys, 95) - np.percentile(ys, 5)) / float(interior_l.shape[0])
        return span_x >= 0.40 and span_y >= 0.40

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
