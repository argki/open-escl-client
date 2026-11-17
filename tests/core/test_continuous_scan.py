"""Unit tests for `ContinuousScanSession` (repeated saves, multi-page PDF, preview handoff)."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock
import pikepdf
from PIL import Image
import pytest

from open_escl_client.core.continuous_scan import ContinuousScanSession, ScanSaveProfile
from open_escl_client.core.paper_size import PaperOrientation, PaperSizeOption
from open_escl_client.core.preview_service import PreviewSession
from open_escl_client.escl.capabilities import InputSourceCaps, InputSourceKind
from open_escl_client.escl.scan_client import EsclScanRequest, EsclScanResult

FIXED_NOW = datetime(2026, 11, 18, 8, 15, 0)


def _sample_jpeg_bytes(color: tuple[int, int, int] = (220, 230, 240)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (300, 450), color=color).save(buf, format="JPEG")
    return buf.getvalue()


def _make_platen_and_a4() -> tuple[InputSourceCaps, PaperSizeOption]:
    platen = InputSourceCaps(
        source=InputSourceKind.PLATEN,
        max_width=3507,
        max_height=5100,
        resolutions=(100, 200, 300, 600),
        color_modes=("RGB24", "Grayscale8"),
    )
    a4 = PaperSizeOption(
        code="A4",
        name="A4",
        width_mm=210.0,
        height_mm=297.0,
        width_units=3507,
        height_units=2480,
        orientation=PaperOrientation.PORTRAIT,
    )
    return platen, a4


def test_continuous_scan_inherits_preview_and_auto_increments_sequence(tmp_path: Path) -> None:
    platen, a4 = _make_platen_and_a4()
    preview = PreviewSession(
        image_bytes=_sample_jpeg_bytes(),
        content_type="image/jpeg",
        image_width_px=300,
        image_height_px=450,
        preview_dpi=100,
        paper_size=a4,
        input_source=InputSourceKind.PLATEN,
    )

    sent_requests: list[EsclScanRequest] = []
    mock_client = MagicMock()

    def _fake_scan(req: EsclScanRequest) -> EsclScanResult:
        sent_requests.append(req)
        return EsclScanResult(
            image_bytes=_sample_jpeg_bytes(),
            content_type="image/jpeg",
            job_url="http://192.168.100.13:80/eSCL/ScanJobs/1",
            request=req,
        )

    mock_client.scan_single_page.side_effect = _fake_scan

    session = ContinuousScanSession.from_preview_session(
        preview,
        base_url="http://192.168.100.13:80/eSCL",
        source_caps=platen,
        save_dir=tmp_path,
        naming_template="Doc_{date}_{seq:03d}",
        file_format="JPEG",
        resolution_dpi=300,
        quality=90,
        scan_client_factory=lambda _url: mock_client,
    )

    assert session.preview_next_filename(now=FIXED_NOW) == "Doc_20261118_001.jpg"

    r1 = session.scan_and_save_next(now=FIXED_NOW)
    r2 = session.scan_and_save_next(now=FIXED_NOW)
    r3 = session.scan_and_save_next(now=FIXED_NOW)

    assert r1.saved_results[0].output_path == tmp_path / "Doc_20261118_001.jpg"
    assert r2.saved_results[0].output_path == tmp_path / "Doc_20261118_002.jpg"
    assert r3.saved_results[0].output_path == tmp_path / "Doc_20261118_003.jpg"
    assert r3.total_saved_files == 3
    assert r3.next_seq == 4
    assert all(req.resolution_dpi == 300 for req in sent_requests)
    assert all(req.width_units == 3507 and req.height_units == 2480 for req in sent_requests)


def test_continuous_scan_multipage_pdf_buffers_and_finalizes(tmp_path: Path) -> None:
    platen, a4 = _make_platen_and_a4()
    mock_client = MagicMock()
    mock_client.scan_single_page.side_effect = [
        EsclScanResult(_sample_jpeg_bytes((240, 200, 200)), "image/jpeg", "u1", EsclScanRequest(3507, 2480, resolution_dpi=300)),
        EsclScanResult(_sample_jpeg_bytes((200, 240, 200)), "image/jpeg", "u2", EsclScanRequest(3507, 2480, resolution_dpi=300)),
    ]

    profile = ScanSaveProfile(
        base_url="http://192.168.100.13:80/eSCL",
        source_caps=platen,
        paper_size=a4,
        save_dir=tmp_path,
        naming_template="Bundle_{date}_{seq:02d}",
        file_format="PDF",
        resolution_dpi=300,
        quality=85,
        multipage_pdf=True,
    )
    session = ContinuousScanSession(profile, scan_client_factory=lambda _url: mock_client)

    step1 = session.scan_and_save_next(now=FIXED_NOW)
    assert step1.saved_results == ()
    assert step1.buffered_page_count == 1

    step2 = session.scan_and_save_next(now=FIXED_NOW)
    assert step2.buffered_page_count == 2
    assert session.pending_page_count == 2

    exported = session.finalize_multipage_pdf(now=FIXED_NOW)
    assert exported.output_path == tmp_path / "Bundle_20261118_01.pdf"
    assert exported.page_count == 2
    assert session.pending_page_count == 0
    assert session.next_seq == 2

    with pikepdf.open(exported.output_path) as pdf:
        assert len(pdf.pages) == 2

    with pytest.raises(ValueError, match="ページ"):
        session.finalize_multipage_pdf(now=FIXED_NOW)


def test_continuous_scan_post_processor_hook_saves_multiple_split_pages(tmp_path: Path) -> None:
    platen, a4 = _make_platen_and_a4()
    mock_client = MagicMock()
    mock_client.scan_single_page.return_value = EsclScanResult(
        _sample_jpeg_bytes(),
        "image/jpeg",
        "u1",
        EsclScanRequest(3507, 2480, resolution_dpi=300),
    )

    profile = ScanSaveProfile(
        base_url="http://192.168.100.13:80/eSCL",
        source_caps=platen,
        paper_size=a4,
        save_dir=tmp_path,
        naming_template="Split_{seq:03d}",
        file_format="PNG",
        resolution_dpi=300,
        post_processor=lambda raw: [raw, raw],
    )
    session = ContinuousScanSession(profile, scan_client_factory=lambda _url: mock_client)

    step = session.scan_and_save_next(now=FIXED_NOW)
    assert len(step.saved_results) == 2
    assert step.saved_results[0].output_path == tmp_path / "Split_001.png"
    assert step.saved_results[1].output_path == tmp_path / "Split_002.png"
    assert step.next_seq == 3
