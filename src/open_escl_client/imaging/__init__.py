"""Image processing pipelines (cropping, deskew, orientation, color calibration)."""

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
from open_escl_client.imaging.orientation import (
    VALID_ROTATIONS,
    AutoOrientationCorrector,
    HeuristicOrientationBackend,
    MacOSVisionOrientationBackend,
    OrientationConfig,
    OrientationDetectorBackend,
    OrientationEstimate,
    OrientationResult,
    RotationDeg,
    normalize_rotation_cw,
    rotate_image_cw,
)

__all__ = [
    "AutoOrientationCorrector",
    "BackgroundMode",
    "CroppedDocument",
    "DeskewConfig",
    "DeskewCropProcessor",
    "DetectionConfig",
    "HeuristicOrientationBackend",
    "MacOSVisionOrientationBackend",
    "MultiDocumentDetector",
    "OrientationConfig",
    "OrientationDetectorBackend",
    "OrientationEstimate",
    "OrientationResult",
    "RotatedCropBox",
    "RotationDeg",
    "VALID_ROTATIONS",
    "decode_image_bytes",
    "encode_image_bytes",
    "normalize_rotation_cw",
    "rotate_image_cw",
]
