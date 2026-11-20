"""Image and PDF exporter supporting DPI metadata, compression quality, and multi-page PDF."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import img2pdf
from PIL import Image, ImageOps

from open_escl_client.config.user_config import normalize_save_format


@dataclass(frozen=True, slots=True)
class ExportOptions:
    """Output format, DPI metadata, and compression settings for saving scanned images."""

    file_format: str = "PDF"
    dpi: int = 300
    quality: int = 85

    @property
    def normalized_format(self) -> str:
        return normalize_save_format(self.file_format)

    @property
    def clamped_dpi(self) -> int:
        return max(50, min(2400, int(self.dpi)))

    @property
    def clamped_quality(self) -> int:
        return max(1, min(100, int(self.quality)))


@dataclass(frozen=True, slots=True)
class ExportResult:
    """Metadata describing a completed file export."""

    output_path: Path
    file_format: str
    dpi: int
    quality: int
    page_count: int
    size_bytes: int


def _to_pil_image(image_data: bytes | Image.Image) -> Image.Image:
    """Load raw image bytes into an EXIF-normalized Pillow `Image`, or copy an existing `Image`."""
    if isinstance(image_data, Image.Image):
        return image_data.copy()
    with Image.open(BytesIO(image_data)) as img:
        img.load()
        transposed = ImageOps.exif_transpose(img)
        return transposed.copy()


def _prepare_opaque_image(img: Image.Image, *, allow_grayscale: bool = True) -> Image.Image:
    """Flatten alpha transparency onto white paper and ensure RGB or grayscale (`L`) mode."""
    if img.mode == "RGB" or (allow_grayscale and img.mode == "L"):
        return img
    if img.mode in ("RGBA", "LA") or "transparency" in img.info:
        background = Image.new("RGB", img.size, (255, 255, 255))
        converted = img.convert("RGBA")
        background.paste(converted, mask=converted.split()[-1])
        return background
    return img.convert("RGB")


def _encode_jpeg_bytes(img: Image.Image, dpi: int, quality: int) -> bytes:
    buf = BytesIO()
    jpeg_img = _prepare_opaque_image(img, allow_grayscale=True)
    jpeg_img.save(
        buf,
        format="JPEG",
        quality=quality,
        dpi=(dpi, dpi),
        optimize=True,
    )
    return buf.getvalue()


def _build_dpi_exif_bytes(dpi: int) -> bytes:
    """Build minimal EXIF payload encoding X/Y DPI resolution in inches for WebP."""
    exif = Image.Exif()
    exif[0x011A] = float(dpi)
    exif[0x011B] = float(dpi)
    exif[0x0128] = 2  # inches
    return exif.tobytes()


class ImageExporter:
    """Exports single or multi-page scanned images across PDF and raster formats."""

    def export_single(
        self,
        image_data: bytes | Image.Image,
        output_path: Path | str,
        options: ExportOptions | None = None,
    ) -> ExportResult:
        """Export a single scanned page to `output_path`."""
        return self.export_pages([image_data], output_path, options=options)

    def export_pages(
        self,
        pages: Sequence[bytes | Image.Image],
        output_path: Path | str,
        options: ExportOptions | None = None,
    ) -> ExportResult:
        """Export one or more scanned pages to `output_path` (multi-page supported for PDF/TIFF)."""
        if not pages:
            raise ValueError("保存するページ画像が空です")

        opts = options if options is not None else ExportOptions()
        fmt = opts.normalized_format
        dpi = opts.clamped_dpi
        quality = opts.clamped_quality

        target_path = Path(output_path).expanduser()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if fmt == "JPEG":
            img = _to_pil_image(pages[0])
            target_path.write_bytes(_encode_jpeg_bytes(img, dpi=dpi, quality=quality))
            page_count = 1
        elif fmt == "PNG":
            img = _to_pil_image(pages[0])
            # Map quality (1..100) inversely to PNG lossless compress_level (9..0)
            compress_level = max(0, min(9, round((100 - quality) * 9 / 99)))
            buf = BytesIO()
            img.save(
                buf,
                format="PNG",
                dpi=(dpi, dpi),
                compress_level=compress_level,
                optimize=True,
            )
            target_path.write_bytes(buf.getvalue())
            page_count = 1
        elif fmt == "TIFF":
            pil_pages = [
                _prepare_opaque_image(_to_pil_image(page), allow_grayscale=True)
                for page in pages
            ]
            buf = BytesIO()
            save_kwargs: dict[str, object] = {
                "format": "TIFF",
                "compression": "tiff_lzw",
                "dpi": (float(dpi), float(dpi)),
            }
            if len(pil_pages) > 1:
                save_kwargs["save_all"] = True
                save_kwargs["append_images"] = pil_pages[1:]
            pil_pages[0].save(buf, **save_kwargs)
            target_path.write_bytes(buf.getvalue())
            page_count = len(pil_pages)
        elif fmt == "WebP":
            img = _prepare_opaque_image(_to_pil_image(pages[0]), allow_grayscale=False)
            buf = BytesIO()
            img.save(
                buf,
                format="WEBP",
                quality=quality,
                exif=_build_dpi_exif_bytes(dpi),
            )
            target_path.write_bytes(buf.getvalue())
            page_count = 1
        elif fmt == "BMP":
            img = _prepare_opaque_image(_to_pil_image(pages[0]), allow_grayscale=True)
            buf = BytesIO()
            img.save(buf, format="BMP", dpi=(dpi, dpi))
            target_path.write_bytes(buf.getvalue())
            page_count = 1
        elif fmt == "GIF":
            rgb = _prepare_opaque_image(_to_pil_image(pages[0]), allow_grayscale=False)
            palette_img = rgb.convert("P", palette=Image.Palette.ADAPTIVE, colors=256)
            buf = BytesIO()
            palette_img.save(buf, format="GIF")
            target_path.write_bytes(buf.getvalue())
            page_count = 1
        elif fmt == "PDF":
            encoded_pages = [
                _encode_jpeg_bytes(_to_pil_image(page), dpi=dpi, quality=quality)
                for page in pages
            ]
            layout_fun = img2pdf.get_fixed_dpi_layout_fun((dpi, dpi))
            pdf_bytes = img2pdf.convert(encoded_pages, layout_fun=layout_fun)
            assert isinstance(pdf_bytes, bytes)
            target_path.write_bytes(pdf_bytes)
            page_count = len(encoded_pages)
        else:
            raise ValueError(f"未対応の保存形式です: {fmt}")

        return ExportResult(
            output_path=target_path,
            file_format=fmt,
            dpi=dpi,
            quality=quality,
            page_count=page_count,
            size_bytes=target_path.stat().st_size,
        )
