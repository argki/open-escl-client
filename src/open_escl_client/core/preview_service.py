"""Fast low-DPI preview scan service and coordinate scaling session (`PreviewScanService`)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from io import BytesIO
from PIL import Image

from open_escl_client.core.paper_size import PaperSizeOption
from open_escl_client.escl.capabilities import (
    THREE_HUNDREDTHS_PER_INCH,
    InputSourceCaps,
    InputSourceKind,
    three_hundredths_inch_to_mm,
)
from open_escl_client.escl.scan_client import EsclScanClient, EsclScanRequest, EsclScanResult


def select_preview_dpi(
    supported_resolutions: Sequence[int],
    *,
    preferred_dpi: int | None = None,
    min_preferred_dpi: int = 75,
    max_preferred_dpi: int = 150,
) -> int:
    """Select the preview DPI from `supported_resolutions` (honoring `preferred_dpi` when set)."""
    if not supported_resolutions:
        return int(preferred_dpi) if preferred_dpi and preferred_dpi > 0 else 100
    sorted_dpis = sorted({int(d) for d in supported_resolutions if int(d) > 0})
    if not sorted_dpis:
        return int(preferred_dpi) if preferred_dpi and preferred_dpi > 0 else 100

    if preferred_dpi is not None and preferred_dpi > 0:
        pref = int(preferred_dpi)
        if pref in sorted_dpis:
            return pref
        return min(sorted_dpis, key=lambda d: (abs(d - pref), d))

    for dpi in sorted_dpis:
        if min_preferred_dpi <= dpi <= max_preferred_dpi:
            return dpi
    return sorted_dpis[0]


def _extract_image_dimensions(image_bytes: bytes) -> tuple[int, int]:
    """Decode image header to obtain `(width_px, height_px)`."""
    with Image.open(BytesIO(image_bytes)) as img:
        return img.size


@dataclass(frozen=True, slots=True)
class PreviewSession:
    """Holds a low-DPI preview scan image and coordinate mapping metadata for main scans."""

    image_bytes: bytes
    content_type: str
    image_width_px: int
    image_height_px: int
    preview_dpi: int
    paper_size: PaperSizeOption
    input_source: InputSourceKind

    def scale_to_target_dpi(self, target_dpi: int) -> float:
        """Return the pixel multiplier from this preview DPI to `target_dpi` (e.g. 300 / 100 = 3.0)."""
        return float(target_dpi) / float(max(1, self.preview_dpi))

    def preview_px_to_escl_units(self, x_px: float, y_px: float) -> tuple[int, int]:
        """Convert preview pixel coordinates `(x_px, y_px)` to eSCL 1/300 inch units."""
        scale = THREE_HUNDREDTHS_PER_INCH / float(max(1, self.preview_dpi))
        u_x = max(0, min(self.paper_size.width_units, int(round(x_px * scale))))
        u_y = max(0, min(self.paper_size.height_units, int(round(y_px * scale))))
        return u_x, u_y

    def preview_px_to_mm(self, x_px: float, y_px: float) -> tuple[float, float]:
        """Convert preview pixel coordinates `(x_px, y_px)` to physical millimeters."""
        u_x, u_y = self.preview_px_to_escl_units(x_px, y_px)
        return three_hundredths_inch_to_mm(u_x), three_hundredths_inch_to_mm(u_y)

    def preview_px_to_target_px(self, x_px: float, y_px: float, target_dpi: int) -> tuple[int, int]:
        """Convert preview pixel coordinates to high-resolution target scan pixel coordinates."""
        factor = self.scale_to_target_dpi(target_dpi)
        return int(round(x_px * factor)), int(round(y_px * factor))


class PreviewScanService:
    """Orchestrates fast low-DPI preview scans independent of the high-DPI save setting."""

    def __init__(
        self,
        scan_client_factory: Callable[[str], EsclScanClient] | None = None,
    ) -> None:
        self._scan_client_factory = scan_client_factory or (lambda base_url: EsclScanClient(base_url))

    def build_preview_request(
        self,
        source_caps: InputSourceCaps,
        paper_size: PaperSizeOption,
        *,
        preview_dpi: int | None = None,
        color_mode: str = "RGB24",
    ) -> EsclScanRequest:
        """Construct an `EsclScanRequest` using `preview_dpi` or the lowest suitable preview DPI."""
        chosen_dpi = select_preview_dpi(source_caps.resolutions, preferred_dpi=preview_dpi)
        chosen_color = color_mode if color_mode in source_caps.color_modes else source_caps.color_modes[0]
        return EsclScanRequest(
            width_units=paper_size.width_units,
            height_units=paper_size.height_units,
            x_offset_units=0,
            y_offset_units=0,
            resolution_dpi=chosen_dpi,
            input_source=source_caps.source,
            color_mode=chosen_color,
            document_format="image/jpeg",
        )

    def execute_preview(
        self,
        base_url: str,
        source_caps: InputSourceCaps,
        paper_size: PaperSizeOption,
        *,
        preview_dpi: int | None = None,
        color_mode: str = "RGB24",
    ) -> PreviewSession:
        """Execute a low-DPI preview scan against `base_url` and return a `PreviewSession`."""
        request = self.build_preview_request(
            source_caps,
            paper_size,
            preview_dpi=preview_dpi,
            color_mode=color_mode,
        )
        client = self._scan_client_factory(base_url)
        scan_result: EsclScanResult = client.scan_single_page(request)
        width_px, height_px = _extract_image_dimensions(scan_result.image_bytes)

        return PreviewSession(
            image_bytes=scan_result.image_bytes,
            content_type=scan_result.content_type,
            image_width_px=width_px,
            image_height_px=height_px,
            preview_dpi=request.resolution_dpi,
            paper_size=paper_size,
            input_source=source_caps.source,
        )
