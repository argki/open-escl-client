"""End-to-end multi-crop, fine deskew, and 90°/180°/270° orientation pipeline.

Bridges `MultiDocumentDetector`, `DeskewCropProcessor`, `AutoOrientationCorrector`,
and preview-to-main-scan DPI box scaling into a `PostProcessor` (`bytes -> list[bytes]`)
compatible with `ContinuousScanSession`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np

from open_escl_client.imaging.multi_crop import (
    DeskewCropProcessor,
    DetectionConfig,
    DetectionPreset,
    MultiDocumentDetector,
    RotatedCropBox,
    RotationDeg,
    decode_image_bytes,
    encode_image_bytes,
    normalize_detection_preset,
    scale_crop_boxes,
)
from open_escl_client.imaging.orientation import (
    AutoOrientationCorrector,
    rotate_image_cw,
)

PostProcessor = Callable[[bytes], list[bytes]]


@dataclass(frozen=True, slots=True)
class MultiCropPipelineConfig:
    """Configuration for multi-document crop, deskew, and orientation post-processing."""

    auto_crop_enabled: bool = True
    multi_document_enabled: bool = True
    auto_orientation_enabled: bool = True
    detection_preset: DetectionPreset = "auto"
    detection_sensitivity: int = 0
    margin_mm: float = 0.0
    target_dpi: float = 300.0
    output_format: Literal["PNG", "JPEG"] = "PNG"
    output_quality: int = 95


@dataclass(frozen=True, slots=True)
class ProcessedDocument:
    """A single cropped, deskewed, and upright-rotated document ready for export."""

    image: np.ndarray
    box: RotatedCropBox
    rotation_cw_deg: RotationDeg
    orientation_source: str
    encoded_bytes: bytes


class MultiCropScanPipeline:
    """Processes scanned pages into individual deskewed & upright-rotated document images."""

    def __init__(
        self,
        config: MultiCropPipelineConfig | None = None,
        *,
        deskew_processor: DeskewCropProcessor | None = None,
        orientation_corrector: AutoOrientationCorrector | None = None,
    ) -> None:
        self.config = config or MultiCropPipelineConfig()
        self._deskew_processor = deskew_processor or DeskewCropProcessor()
        self._orientation_corrector = orientation_corrector or AutoOrientationCorrector()
        self._last_processed_documents: tuple[ProcessedDocument, ...] = ()

    @property
    def last_processed_documents(self) -> tuple[ProcessedDocument, ...]:
        """Return documents produced by the most recent `process_documents` invocation."""
        return self._last_processed_documents

    def process_documents(
        self,
        image: np.ndarray | bytes,
        *,
        preview_boxes: Sequence[RotatedCropBox] | None = None,
    ) -> list[ProcessedDocument]:
        """Crop, deskew, and orient one scanned platen image into 1 or more documents.

        Args:
            image: Raw high-DPI scanned image (`bytes` or BGR `np.ndarray`).
            preview_boxes: Optional user-confirmed/adjusted boxes from preview resolution.
                When provided, these boxes (including margin offsets, manual fine-angle
                tweaks, and manual/auto 90° rotations) are scaled to `image`'s resolution
                and applied directly instead of re-detecting from scratch.
        """
        bgr = decode_image_bytes(image) if isinstance(image, bytes) else image
        if bgr is None or bgr.size == 0:
            raise ValueError("Scanned image must not be empty")

        img_h, img_w = bgr.shape[:2]
        dpi = float(self.config.target_dpi)

        # Case 1: Auto-cropping disabled — keep full image, optionally auto-orient it.
        if not self.config.auto_crop_enabled:
            full_box = RotatedCropBox(
                center_x=img_w * 0.5,
                center_y=img_h * 0.5,
                width=float(img_w),
                height=float(img_h),
                angle_deg=0.0,
                index=1,
                source_width=img_w,
                source_height=img_h,
                source_dpi=dpi,
            )
            docs = [self._orient_and_encode(bgr, full_box, forced_rotation=None)]
            self._last_processed_documents = tuple(docs)
            return docs

        # Case 2: Preview-confirmed boxes supplied — scale them to main-scan resolution.
        if preview_boxes:
            scaled_boxes = scale_crop_boxes(
                preview_boxes,
                target_width=img_w,
                target_height=img_h,
                target_dpi=dpi,
            )
            results: list[ProcessedDocument] = []
            for box in scaled_boxes:
                cropped = self._deskew_processor.crop_box(
                    bgr,
                    box,
                    refine_angle=False,
                    dpi=dpi,
                )
                # Respect preview-confirmed rotation (manual override takes precedence over auto).
                if box.manual_rotation_cw_deg is not None:
                    rot = box.effective_rotation_cw_deg
                    rotated_img = rotate_image_cw(cropped.image, rot)
                    results.append(
                        ProcessedDocument(
                            image=rotated_img,
                            box=cropped.box,
                            rotation_cw_deg=rot,
                            orientation_source="manual",
                            encoded_bytes=self._encode(rotated_img),
                        )
                    )
                elif self.config.auto_orientation_enabled:
                    if box.auto_rotation_cw_deg != 0:
                        rot = box.effective_rotation_cw_deg
                        rotated_img = rotate_image_cw(cropped.image, rot)
                        results.append(
                            ProcessedDocument(
                                image=rotated_img,
                                box=cropped.box,
                                rotation_cw_deg=rot,
                                orientation_source="preview_auto",
                                encoded_bytes=self._encode(rotated_img),
                            )
                        )
                    else:
                        # Re-check at high DPI in case low-DPI preview could not read tiny text.
                        results.append(
                            self._orient_and_encode(
                                cropped.image,
                                cropped.box,
                                forced_rotation=None,
                            )
                        )
                else:
                    results.append(
                        ProcessedDocument(
                            image=cropped.image,
                            box=cropped.box,
                            rotation_cw_deg=0,
                            orientation_source="disabled",
                            encoded_bytes=self._encode(cropped.image),
                        )
                    )
            self._last_processed_documents = tuple(results)
            return results

        # Case 3: Fresh automatic detection on main-scan image (e.g. continuous scan 2nd+ page).
        detector = MultiDocumentDetector(
            DetectionConfig(
                multi_document=self.config.multi_document_enabled,
                detection_preset=normalize_detection_preset(self.config.detection_preset),
                detection_sensitivity=self.config.detection_sensitivity,
            )
        )
        detected = detector.detect(bgr, dpi=dpi)
        if not detected:
            fallback_box = RotatedCropBox(
                center_x=img_w * 0.5,
                center_y=img_h * 0.5,
                width=float(img_w),
                height=float(img_h),
                angle_deg=0.0,
                index=1,
                source_width=img_w,
                source_height=img_h,
                source_dpi=dpi,
            )
            docs = [self._orient_and_encode(bgr, fallback_box, forced_rotation=None)]
            self._last_processed_documents = tuple(docs)
            return docs

        results = []
        for raw_box in detected:
            box_with_margin = (
                raw_box.with_uniform_margin(self.config.margin_mm, unit="mm")
                if abs(self.config.margin_mm) > 1e-6
                else raw_box
            )
            cropped = self._deskew_processor.crop_box(
                bgr,
                box_with_margin,
                refine_angle=True,
                dpi=dpi,
            )
            results.append(
                self._orient_and_encode(cropped.image, cropped.box, forced_rotation=None)
            )
        self._last_processed_documents = tuple(results)
        return results

    def as_post_processor(
        self,
        *,
        preview_boxes: Sequence[RotatedCropBox] | None = None,
        consume_preview_boxes_once: bool = True,
    ) -> PostProcessor:
        """Return a `ContinuousScanSession`-compatible `PostProcessor` callable.

        When `consume_preview_boxes_once` is True (default), `preview_boxes` are applied
        to the first main scan immediately following preview confirmation, and subsequent
        continuous scans automatically re-detect freshly placed documents.
        """
        pending_boxes: list[RotatedCropBox] | None = (
            list(preview_boxes) if preview_boxes else None
        )

        def _post_process(raw_bytes: bytes) -> list[bytes]:
            nonlocal pending_boxes
            boxes_to_use = pending_boxes
            if consume_preview_boxes_once:
                pending_boxes = None
            docs = self.process_documents(raw_bytes, preview_boxes=boxes_to_use)
            return [doc.encoded_bytes for doc in docs]

        return _post_process

    def _orient_and_encode(
        self,
        bgr: np.ndarray,
        box: RotatedCropBox,
        *,
        forced_rotation: RotationDeg | None,
    ) -> ProcessedDocument:
        if forced_rotation is not None:
            rotated = rotate_image_cw(bgr, forced_rotation)
            return ProcessedDocument(
                image=rotated,
                box=box.with_manual_rotation(forced_rotation),
                rotation_cw_deg=forced_rotation,
                orientation_source="manual",
                encoded_bytes=self._encode(rotated),
            )

        if self.config.auto_orientation_enabled:
            orient_res = self._orientation_corrector.correct(bgr)
            updated_box = box.with_auto_rotation(orient_res.rotation_cw_deg)
            return ProcessedDocument(
                image=orient_res.image,
                box=updated_box,
                rotation_cw_deg=orient_res.rotation_cw_deg,
                orientation_source=orient_res.estimate.backend_name,
                encoded_bytes=self._encode(orient_res.image),
            )

        return ProcessedDocument(
            image=bgr,
            box=box.with_auto_rotation(0),
            rotation_cw_deg=0,
            orientation_source="disabled",
            encoded_bytes=self._encode(bgr),
        )

    def _encode(self, bgr: np.ndarray) -> bytes:
        return encode_image_bytes(
            bgr,
            fmt=self.config.output_format,
            quality=self.config.output_quality,
        )
