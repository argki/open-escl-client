"""Unit tests for `ImageExporter` (JPEG / PNG / PDF, DPI metadata, quality compression)."""

from __future__ import annotations

from pathlib import Path
import numpy as np
import pikepdf
from PIL import Image
import pytest

from open_escl_client.core.exporter import ExportOptions, ImageExporter


def _make_textured_image(width: int = 600, height: int = 900, seed: int = 42) -> Image.Image:
    """Create a realistic textured RGB image so JPEG compression quality changes file size noticeably."""
    rng = np.random.default_rng(seed)
    grad_x = np.linspace(40, 220, width, dtype=np.float32)[None, :, None]
    grad_y = np.linspace(30, 210, height, dtype=np.float32)[:, None, None]
    noise = rng.integers(-25, 25, size=(height, width, 3), dtype=np.int16)
    arr = np.clip((grad_x + grad_y) * 0.5 + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, mode="RGB")


def test_export_jpeg_embeds_dpi_and_respects_quality(tmp_path: Path) -> None:
    exporter = ImageExporter()
    img = _make_textured_image(600, 900)

    high_path = tmp_path / "high.jpg"
    low_path = tmp_path / "low.jpg"

    res_high = exporter.export_single(img, high_path, ExportOptions(file_format="JPEG", dpi=300, quality=95))
    res_low = exporter.export_single(img, low_path, ExportOptions(file_format="JPEG", dpi=300, quality=35))

    assert res_high.file_format == "JPEG"
    assert res_high.page_count == 1
    assert res_low.size_bytes < res_high.size_bytes

    with Image.open(high_path) as loaded:
        assert loaded.format == "JPEG"
        assert loaded.size == (600, 900)
        dpi_x, dpi_y = loaded.info["dpi"]
        assert pytest.approx(dpi_x, abs=1.0) == 300
        assert pytest.approx(dpi_y, abs=1.0) == 300


def test_export_png_embeds_dpi(tmp_path: Path) -> None:
    exporter = ImageExporter()
    img = _make_textured_image(300, 450)
    png_path = tmp_path / "nested" / "out.png"

    res = exporter.export_single(img, png_path, ExportOptions(file_format="PNG", dpi=600, quality=80))
    assert res.file_format == "PNG"
    assert png_path.is_file()

    with Image.open(png_path) as loaded:
        assert loaded.format == "PNG"
        assert loaded.size == (300, 450)
        dpi_x, dpi_y = loaded.info["dpi"]
        assert pytest.approx(dpi_x, abs=1.5) == 600
        assert pytest.approx(dpi_y, abs=1.5) == 600


def test_export_pdf_physical_size_quality_and_multipage(tmp_path: Path) -> None:
    exporter = ImageExporter()
    # 600 x 900 px at 300 DPI = 2.0 x 3.0 inches = 144 x 216 PDF points
    page1 = _make_textured_image(600, 900, seed=1)
    page2 = _make_textured_image(600, 900, seed=2)

    pdf_high = tmp_path / "multi_high.pdf"
    pdf_low = tmp_path / "multi_low.pdf"

    res_high = exporter.export_pages(
        [page1, page2],
        pdf_high,
        ExportOptions(file_format="PDF", dpi=300, quality=95),
    )
    res_low = exporter.export_pages(
        [page1, page2],
        pdf_low,
        ExportOptions(file_format="PDF", dpi=300, quality=35),
    )

    assert res_high.file_format == "PDF"
    assert res_high.page_count == 2
    assert res_low.size_bytes < res_high.size_bytes

    with pikepdf.open(pdf_high) as pdf:
        assert len(pdf.pages) == 2
        mediabox = pdf.pages[0].MediaBox
        width_pt = float(mediabox[2]) - float(mediabox[0])
        height_pt = float(mediabox[3]) - float(mediabox[1])
        assert pytest.approx(width_pt, abs=1.0) == 144.0
        assert pytest.approx(height_pt, abs=1.0) == 216.0


def test_export_pages_empty_raises(tmp_path: Path) -> None:
    exporter = ImageExporter()
    with pytest.raises(ValueError, match="空"):
        exporter.export_pages([], tmp_path / "empty.pdf")
