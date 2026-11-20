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


def test_export_tiff_single_and_multipage_with_lzw_and_dpi(tmp_path: Path) -> None:
    exporter = ImageExporter()
    page1 = _make_textured_image(300, 420, seed=10)
    page2 = _make_textured_image(300, 420, seed=20)

    tiff_path = tmp_path / "archive.tif"
    res = exporter.export_pages(
        [page1, page2],
        tiff_path,
        ExportOptions(file_format="TIFF", dpi=400, quality=85),
    )
    assert res.file_format == "TIFF"
    assert res.page_count == 2
    assert tiff_path.is_file()

    with Image.open(tiff_path) as loaded:
        assert loaded.format == "TIFF"
        assert getattr(loaded, "n_frames", 1) == 2
        assert loaded.info.get("compression") == "tiff_lzw"
        dpi_x, dpi_y = loaded.info["dpi"]
        assert pytest.approx(dpi_x, abs=1.0) == 400.0
        assert pytest.approx(dpi_y, abs=1.0) == 400.0


def test_export_webp_bmp_gif_formats_and_encode_image_bytes(tmp_path: Path) -> None:
    from open_escl_client.imaging.multi_crop import encode_image_bytes

    exporter = ImageExporter()
    img = _make_textured_image(320, 400, seed=7)
    rgba_img = img.convert("RGBA")

    webp_high = tmp_path / "high.webp"
    webp_low = tmp_path / "low.webp"
    res_hi = exporter.export_single(
        rgba_img, webp_high, ExportOptions(file_format="WebP", dpi=300, quality=95)
    )
    res_lo = exporter.export_single(
        rgba_img, webp_low, ExportOptions(file_format="WebP", dpi=300, quality=30)
    )
    assert res_hi.file_format == "WebP"
    assert res_lo.size_bytes < res_hi.size_bytes
    with Image.open(webp_high) as loaded_webp:
        assert loaded_webp.format == "WEBP"
        exif = loaded_webp.getexif()
        assert pytest.approx(float(exif[0x011A]), abs=1.0) == 300.0

    bmp_path = tmp_path / "out.bmp"
    res_bmp = exporter.export_single(
        rgba_img, bmp_path, ExportOptions(file_format="BMP", dpi=300, quality=90)
    )
    assert res_bmp.file_format == "BMP"
    with Image.open(bmp_path) as loaded_bmp:
        assert loaded_bmp.format == "BMP"
        assert pytest.approx(loaded_bmp.info["dpi"][0], abs=1.0) == 300.0

    gif_path = tmp_path / "out.gif"
    res_gif = exporter.export_single(
        rgba_img, gif_path, ExportOptions(file_format="GIF", dpi=150, quality=80)
    )
    assert res_gif.file_format == "GIF"
    with Image.open(gif_path) as loaded_gif:
        assert loaded_gif.format == "GIF"
        assert loaded_gif.mode == "P"

    bgr = np.array(img)[:, :, ::-1]
    for fmt in ("PNG", "JPEG", "TIFF", "WebP", "BMP", "GIF"):
        encoded = encode_image_bytes(bgr, fmt=fmt, quality=85)
        assert len(encoded) > 50
