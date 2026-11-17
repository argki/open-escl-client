"""Unit tests for `PreviewScanService`, low-DPI selection, and `PreviewSession` coordinate mapping."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock
from PIL import Image
import pytest

from open_escl_client.core.paper_size import PaperOrientation, PaperSizeOption
from open_escl_client.core.preview_service import (
    PreviewScanService,
    select_preview_dpi,
)
from open_escl_client.escl.capabilities import InputSourceCaps, InputSourceKind
from open_escl_client.escl.scan_client import EsclScanRequest, EsclScanResult


def _create_sample_jpeg(width: int = 827, height: int = 1169) -> bytes:
    buf = BytesIO()
    img = Image.new("RGB", (width, height), color=(240, 245, 250))
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_select_preview_dpi_chooses_lowest_in_75_to_150_range() -> None:
    # Brother scanner with (100, 150, 200, 300, 600, 1200) -> 100 dpi
    assert select_preview_dpi((100, 150, 200, 300, 600, 1200)) == 100
    # Scanner supporting 75 dpi -> 75 dpi
    assert select_preview_dpi((75, 100, 150, 300, 600)) == 75
    # Scanner whose lowest is 200 dpi -> falls back to 200 dpi
    assert select_preview_dpi((200, 300, 600)) == 200


def test_preview_scan_service_executes_at_low_dpi_and_creates_session() -> None:
    sample_jpeg = _create_sample_jpeg(827, 1169)
    platen_caps = InputSourceCaps(
        source=InputSourceKind.PLATEN,
        max_width=3508,
        max_height=5100,
        resolutions=(100, 150, 200, 300, 600, 1200),
        color_modes=("RGB24", "Grayscale8"),
    )
    a4_option = PaperSizeOption(
        code="A4_LANDSCAPE",
        name="A4",
        width_mm=297.0,
        height_mm=210.0,
        width_units=2480,
        height_units=3508,
        orientation=PaperOrientation.LANDSCAPE,
    )

    mock_scan_client = MagicMock()

    def _fake_scan(req: EsclScanRequest) -> EsclScanResult:
        return EsclScanResult(
            image_bytes=sample_jpeg,
            content_type="image/jpeg",
            job_url="http://192.168.1.50:80/eSCL/ScanJobs/1",
            request=req,
        )

    mock_scan_client.scan_single_page.side_effect = _fake_scan
    service = PreviewScanService(scan_client_factory=lambda _url: mock_scan_client)

    session = service.execute_preview("http://192.168.1.50:80/eSCL", platen_caps, a4_option)

    assert session.preview_dpi == 100
    assert session.image_width_px == 827
    assert session.image_height_px == 1169
    assert session.scale_to_target_dpi(300) == 3.0
    assert session.scale_to_target_dpi(600) == 6.0

    # 100px at 100 DPI = 1.0 inch = 300 eSCL units (1/300") = 25.4 mm
    u_x, u_y = session.preview_px_to_escl_units(100.0, 200.0)
    assert (u_x, u_y) == (300, 600)

    mm_x, mm_y = session.preview_px_to_mm(100.0, 200.0)
    assert pytest.approx(mm_x, abs=0.05) == 25.4
    assert pytest.approx(mm_y, abs=0.05) == 50.8

    target_x, target_y = session.preview_px_to_target_px(100.0, 200.0, 300)
    assert (target_x, target_y) == (300, 600)
