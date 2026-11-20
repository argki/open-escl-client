"""Unit and platform tests for Japanese OCR engine abstraction and macOS Vision OCR backend."""

from __future__ import annotations

import platform
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from open_escl_client.imaging.multi_crop import encode_image_bytes
from open_escl_client.imaging.ocr import (
    JapaneseOcrEngine,
    MacOSVisionOcrBackend,
    NoOpOcrBackend,
    OcrBoundingBox,
    OcrConfig,
    OcrResult,
    OcrTextBlock,
    RapidOcrBackend,
)


def _find_font(candidates: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont | None:
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return None


def _render_sample_japanese_invoice(width: int = 800, height: int = 1000) -> np.ndarray:
    """Render a synthetic Japanese invoice image in BGR uint8 format."""
    img = Image.new("RGB", (width, height), (250, 250, 248))
    draw = ImageDraw.Draw(img)

    jp_font_paths = (
        "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    )
    title_font = _find_font(jp_font_paths, 44)
    body_font = _find_font(jp_font_paths, 26)
    if title_font is None or body_font is None:
        pytest.skip("System Japanese font not available for rendering synthetic document")

    draw.text((80, 70), "御請求書", fill=(15, 15, 15), font=title_font)
    lines = [
        "発行日: 2026年11月20日",
        "株式会社テスト商事 御中",
        "件名: 複合機スキャナ導入費用",
        "合計金額: 54,000円",
    ]
    y = 175
    for line in lines:
        draw.text((80, y), line, fill=(25, 25, 25), font=body_font)
        y += 56

    rgb = np.array(img, dtype=np.uint8)
    return rgb[:, :, ::-1].copy()


class _FakeOcrBackend:
    def __init__(
        self,
        name: str = "fake_ocr",
        *,
        available: bool = True,
        should_fail: bool = False,
        blocks: tuple[OcrTextBlock, ...] = (),
    ) -> None:
        self._name = name
        self._available = available
        self._should_fail = should_fail
        self._blocks = blocks

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return self._available

    def recognize(self, image: np.ndarray, config: OcrConfig) -> OcrResult:
        if self._should_fail:
            raise RuntimeError("Simulated backend crash")
        h, w = image.shape[:2]
        text = "\n".join(b.text for b in self._blocks)
        return OcrResult(
            blocks=self._blocks,
            full_text=text,
            backend_name=self._name,
            image_width=w,
            image_height=h,
            applied=True,
        )


def test_japanese_ocr_engine_selects_available_backend_and_falls_back_gracefully() -> None:
    sample_block = OcrTextBlock(
        text="請求書",
        bbox=OcrBoundingBox(x=0.1, y=0.05, width=0.3, height=0.06),
        confidence=0.95,
    )
    failing = _FakeOcrBackend("broken", available=True, should_fail=True)
    unavailable = _FakeOcrBackend("skipped", available=False, blocks=(sample_block,))
    healthy = _FakeOcrBackend("healthy", available=True, blocks=(sample_block,))

    engine = JapaneseOcrEngine(backends=[unavailable, failing, healthy])
    assert engine.available_backend_names() == ["broken", "healthy"]

    img = np.full((200, 300, 3), 255, dtype=np.uint8)
    res = engine.recognize(img)
    assert res.applied is True
    assert res.backend_name == "healthy"
    assert res.full_text == "請求書"
    assert res.has_text is True

    # Also accepts encoded bytes cleanly
    png_bytes = encode_image_bytes(img, fmt="PNG")
    res_bytes = engine.recognize(png_bytes)
    assert res_bytes.full_text == "請求書"

    # Graceful no-op fallback when all real backends fail/unavailable
    noop_engine = JapaneseOcrEngine(backends=[failing, NoOpOcrBackend()])
    noop_res = noop_engine.recognize(img)
    assert noop_res.applied is False
    assert noop_res.backend_name == "noop_fallback"
    assert noop_res.full_text == ""
    assert noop_res.has_text is False


def test_rapidocr_backend_adapter_normalizes_quads_and_orders_reading_flow() -> None:
    def _mock_rapidocr_factory():
        def _run(_img: np.ndarray):
            # Intentionally out-of-order entries + below-threshold noise
            return (
                [
                    ([[50, 200], [350, 200], [350, 240], [50, 240]], "2026年11月20日", 0.92),
                    ([[100, 50], [300, 50], [300, 110], [100, 110]], "領収書", 0.98),
                    ([[10, 300], [40, 300], [40, 320], [10, 320]], "noise", 0.05),
                ],
                [0.01],
            )

        return _run

    backend = RapidOcrBackend(engine_factory=_mock_rapidocr_factory)
    assert backend.is_available() is True

    img = np.full((400, 500, 3), 255, dtype=np.uint8)
    res = JapaneseOcrEngine(
        config=OcrConfig(min_confidence=0.2),
        backends=[backend],
    ).recognize(img)

    assert res.applied is True
    assert res.backend_name == "rapidocr_onnx"
    assert [b.text for b in res.blocks] == ["領収書", "2026年11月20日"]
    assert res.blocks[0].height_norm > res.blocks[1].height_norm
    assert 0.0 <= res.blocks[0].bbox.x <= 1.0
    assert 0.0 <= res.blocks[0].bbox.y <= 1.0


@pytest.mark.skipif(
    platform.system() != "Darwin" or not MacOSVisionOcrBackend().is_available(),
    reason="macOS Vision OCR backend requires macOS with swiftc",
)
def test_macos_vision_ocr_recognizes_japanese_invoice_with_layout() -> None:
    """Real macOS Vision VNRecognizeTextRequest (ja-JP) reads Japanese title & body accurately."""
    invoice = _render_sample_japanese_invoice()
    backend = MacOSVisionOcrBackend()
    engine = JapaneseOcrEngine(
        config=OcrConfig(
            recognition_languages=("ja-JP", "en-US"),
            recognition_level="accurate",
            uses_language_correction=True,
        ),
        backends=[backend],
    )
    res = engine.recognize(invoice)

    assert res.applied is True
    assert res.backend_name == "macos_vision_ocr"
    assert res.has_text is True
    assert "御請求書" in res.full_text or "請求書" in res.full_text
    assert "2026" in res.full_text
    assert len(res.blocks) >= 3
    # Top title block sits near upper portion of image (top-left origin y < 0.2)
    title_block = next(b for b in res.blocks if "請求" in b.text)
    assert title_block.bbox.y < 0.25
    assert title_block.height_norm > 0.02
