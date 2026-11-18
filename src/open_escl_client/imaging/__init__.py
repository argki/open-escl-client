"""Image processing pipelines (cropping, deskew, color calibration, bleed-through removal)."""

from open_escl_client.imaging.multi_crop import (
    BackgroundMode,
    CroppedDocument,
    DeskewConfig,
    DeskewCropProcessor,
    DetectionConfig,
    MultiDocumentDetector,
    RotatedCropBox,
    decode_image_bytes,
    encode_image_bytes,
)

__all__ = [
    "BackgroundMode",
    "CroppedDocument",
    "DeskewConfig",
    "DeskewCropProcessor",
    "DetectionConfig",
    "MultiDocumentDetector",
    "RotatedCropBox",
    "decode_image_bytes",
    "encode_image_bytes",
]
