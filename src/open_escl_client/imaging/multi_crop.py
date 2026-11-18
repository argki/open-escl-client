"""Multi-document detection and high-precision fine deskew / cropping engine.

Detects one or more arbitrarily rotated paper documents placed on a scanner platen
(light or dark background), refines each document's fine skew angle using edge lines
and internal text/structure alignment, and extracts straight rectangular crops using
single-pass subpixel affine warping.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal

import cv2
import numpy as np

BackgroundMode = Literal["auto", "light", "dark"]


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

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    def corners(self) -> np.ndarray:
        """Return the 4 corners as a `(4, 2)` float32 array in TL, TR, BR, BL order."""
        half_w = self.width * 0.5
        half_h = self.height * 0.5
        rad = math.radians(self.angle_deg)
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
                self.center_x + dx * cos_a - dy * sin_a,
                self.center_y + dx * sin_a + dy * cos_a,
            )
            for dx, dy in local
        ]
        return np.array(pts, dtype=np.float32)

    def bounding_rect(self) -> tuple[float, float, float, float]:
        """Return axis-aligned bounding box `(x, y, w, h)` in image coordinates."""
        pts = self.corners()
        min_x = float(np.min(pts[:, 0]))
        min_y = float(np.min(pts[:, 1]))
        max_x = float(np.max(pts[:, 0]))
        max_y = float(np.max(pts[:, 1]))
        return min_x, min_y, max_x - min_x, max_y - min_y


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

    def detect(self, image: np.ndarray | bytes) -> list[RotatedCropBox]:
        """Detect rotated document bounding boxes in `image`.

        Args:
            image: OpenCV BGR/Gray ndarray or raw encoded image bytes.

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
    ) -> list[CroppedDocument]:
        """Detect (if `boxes` is omitted), refine skew, and crop all documents."""
        bgr = _ensure_bgr(decode_image_bytes(image) if isinstance(image, bytes) else image)
        target_boxes = boxes if boxes is not None else self.detector.detect(bgr)
        return [self.crop_box(bgr, box) for box in target_boxes]

    def crop_box(
        self,
        image: np.ndarray | bytes,
        box: RotatedCropBox,
        *,
        refine_angle: bool | None = None,
    ) -> CroppedDocument:
        """Refine a single box's tilt angle (optional) and extract the straightened crop."""
        bgr = _ensure_bgr(decode_image_bytes(image) if isinstance(image, bytes) else image)
        do_refine = self.config.refine_angle if refine_angle is None else refine_angle

        raw_angle = float(box.angle_deg)
        residual = 0.0
        method = "coarse"

        if do_refine:
            padded_w = max(16.0, box.width * (1.0 + 2.0 * self.config.padding_ratio))
            padded_h = max(16.0, box.height * (1.0 + 2.0 * self.config.padding_ratio))
            coarse_patch = self._warp_crop(
                bgr,
                center=(box.center_x, box.center_y),
                width=padded_w,
                height=padded_h,
                angle_deg=raw_angle,
                interpolation=cv2.INTER_LINEAR,
            )
            residual, method = self.estimate_residual_angle(
                coarse_patch,
                inner_width=box.width,
                inner_height=box.height,
            )

        refined_angle = raw_angle + residual
        cropped = self._warp_crop(
            bgr,
            center=(box.center_x, box.center_y),
            width=box.width,
            height=box.height,
            angle_deg=refined_angle,
            interpolation=self.config.interpolation,
        )

        refined_box = replace(box, angle_deg=refined_angle)
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
