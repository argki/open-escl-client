"""Unit tests for 0° / 90° / 180° / 270° automatic orientation detection and rotation."""

from __future__ import annotations

import platform
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from open_escl_client.imaging.multi_crop import encode_image_bytes
from open_escl_client.imaging.orientation import (
    AutoOrientationCorrector,
    HeuristicOrientationBackend,
    MacOSVisionOrientationBackend,
    OrientationConfig,
    OrientationEstimate,
    normalize_rotation_cw,
    rotate_image_cw,
)


def _find_font(candidates: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont | None:
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return None


def _render_english_document(width: int = 720, height: int = 960) -> np.ndarray:
    """Render a realistic left-aligned English document onto a BGR ndarray."""
    canvas = np.full((height, width, 3), (248, 248, 246), dtype=np.uint8)
    lines = [
        ("INVOICE #2026-1118", 1.15, 2),
        ("Open eSCL Client Project", 0.85, 2),
        ("", 0.7, 1),
        ("Date: November 18, 2026", 0.72, 2),
        ("Customer: Sample Corporation Ltd.", 0.72, 2),
        ("Description of scanned items:", 0.72, 2),
        ("1. Brother MFC-J6580CDW flatbed", 0.68, 2),
        ("2. Multi-crop and deskew engine", 0.68, 2),
        ("3. Automatic 90/180/270 rotation", 0.68, 2),
        ("Subtotal: 12,800 JPY", 0.72, 2),
        ("Total due:", 0.75, 2),
        ("Thank you.", 0.72, 2),
    ]
    y = 95
    for text, scale, thickness in lines:
        if text:
            cv2.putText(
                canvas,
                text,
                (65, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                scale,
                (25, 25, 25),
                thickness,
                cv2.LINE_AA,
            )
        y += int(round(56 * scale))
    return canvas


def _render_japanese_document(width: int = 720, height: int = 960) -> np.ndarray:
    """Render a realistic left-aligned Japanese + English document using system fonts."""
    img = Image.new("RGB", (width, height), (248, 248, 246))
    draw = ImageDraw.Draw(img)

    jp_font_paths = (
        "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    )
    title_font = _find_font(jp_font_paths, 38)
    body_font = _find_font(jp_font_paths, 26)
    if title_font is None or body_font is None:
        return _render_english_document(width, height)

    draw.text((64, 70), "御請求書 / Invoice", fill=(20, 20, 20), font=title_font)
    body_lines = [
        "発行日: 2026年11月18日",
        "宛先: 株式会社オープン・スキャン御中",
        "件名: スキャナクライアント開発一式",
        "1. 自動トリミングおよび傾き補正モジュール",
        "2. スキャン内容に基づく自動回転機能",
        "3. 連続スキャン保存ワークフロー統合",
        "合計金額: 128,000円（税込）",
        "お支払期限: 2026年12月末日",
        "備考: よろしくお願いいたします。",
    ]
    y = 160
    for line in body_lines:
        draw.text((64, y), line, fill=(30, 30, 30), font=body_font)
        y += 58

    rgb = np.array(img, dtype=np.uint8)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _create_photo_like_image(width: int = 640, height: int = 480) -> np.ndarray:
    """Create a smooth color landscape-like graphic without any text."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(height):
        t = y / float(height - 1)
        img[y, :] = (
            int(210 * (1.0 - t) + 50 * t),
            int(160 * (1.0 - t) + 120 * t),
            int(100 * (1.0 - t) + 65 * t),
        )
    cv2.circle(img, (width // 3, height // 3), 55, (90, 210, 245), thickness=-1)
    cv2.ellipse(
        img,
        (width * 2 // 3, height * 3 // 4),
        (160, 70),
        0,
        0,
        360,
        (45, 95, 55),
        thickness=-1,
    )
    return img


def test_lossless_90_degree_rotation_roundtrip() -> None:
    """Rotating by 90/180/270 and applying the inverse restores exact pixels."""
    img = np.arange(30 * 40 * 3, dtype=np.uint8).reshape((30, 40, 3))

    for deg in (0, 90, 180, 270):
        rotated = rotate_image_cw(img, deg)
        inv = normalize_rotation_cw(-deg)
        restored = rotate_image_cw(rotated, inv)
        assert np.array_equal(restored, img)

    assert normalize_rotation_cw(360) == 0
    assert normalize_rotation_cw(-90) == 270
    assert normalize_rotation_cw(450) == 90


@pytest.mark.parametrize("input_cw_deg", [0, 90, 180, 270])
def test_heuristic_backend_detects_all_four_orientations(input_cw_deg: int) -> None:
    """Cross-platform OpenCV heuristic detects 0/90/180/270 for left-aligned text docs."""
    upright = _render_english_document()
    scanned = rotate_image_cw(upright, input_cw_deg)
    expected_fix = normalize_rotation_cw(-input_cw_deg)

    corrector = AutoOrientationCorrector(
        config=OrientationConfig(prefer_native_backend=False, min_confidence=0.2),
        backends=[HeuristicOrientationBackend()],
    )
    result = corrector.correct(scanned)

    assert result.estimate.backend_name == "heuristic_opencv"
    assert result.estimate.applied is True
    assert result.rotation_cw_deg == expected_fix
    assert np.array_equal(result.image, upright)


def test_heuristic_backend_refuses_blank_and_photo_images() -> None:
    """Blank pages and text-free photos must not be rotated arbitrarily."""
    blank = np.full((600, 800, 3), (248, 248, 248), dtype=np.uint8)
    photo = _create_photo_like_image()

    corrector = AutoOrientationCorrector(
        config=OrientationConfig(prefer_native_backend=False),
        backends=[HeuristicOrientationBackend()],
    )

    for img in (blank, photo):
        res = corrector.correct(img)
        assert res.estimate.applied is False
        assert res.rotation_cw_deg == 0
        assert np.array_equal(res.image, img)


@pytest.fixture(scope="module")
def shared_vision_backend() -> MacOSVisionOrientationBackend:
    # Uses the default content-hashed cache so repeated test runs reuse the compiled binary.
    return MacOSVisionOrientationBackend()


@pytest.mark.skipif(
    platform.system() != "Darwin" or not MacOSVisionOrientationBackend().is_available(),
    reason="macOS Vision backend requires macOS with swiftc",
)
@pytest.mark.parametrize("input_cw_deg", [0, 90, 180, 270])
def test_macos_vision_backend_detects_japanese_english_all_four_orientations(
    input_cw_deg: int,
    shared_vision_backend: MacOSVisionOrientationBackend,
) -> None:
    """Real macOS Vision framework detects 0/90/180/270 on Japanese+English documents."""
    upright = _render_japanese_document()
    scanned = rotate_image_cw(upright, input_cw_deg)
    expected_fix = normalize_rotation_cw(-input_cw_deg)

    corrector = AutoOrientationCorrector(
        config=OrientationConfig(prefer_native_backend=True, min_confidence=0.25),
        backends=[shared_vision_backend],
    )
    result = corrector.correct(scanned)

    assert result.estimate.backend_name == "macos_vision"
    assert result.estimate.applied is True
    assert result.rotation_cw_deg == expected_fix
    assert result.estimate.confidence >= 0.8
    assert np.array_equal(result.image, upright)


@pytest.mark.skipif(
    platform.system() != "Darwin" or not MacOSVisionOrientationBackend().is_available(),
    reason="macOS Vision backend requires macOS with swiftc",
)
def test_macos_vision_backend_does_not_rotate_photo(
    shared_vision_backend: MacOSVisionOrientationBackend,
) -> None:
    photo = _create_photo_like_image()
    corrector = AutoOrientationCorrector(
        config=OrientationConfig(prefer_native_backend=True, min_confidence=0.25),
        backends=[shared_vision_backend, HeuristicOrientationBackend()],
    )
    result = corrector.correct(photo)

    assert result.estimate.applied is False
    assert result.rotation_cw_deg == 0
    assert np.array_equal(result.image, photo)


class _FailingBackend:
    @property
    def name(self) -> str:
        return "failing_mock"

    def is_available(self) -> bool:
        return True

    def estimate(self, image: np.ndarray, config: OrientationConfig) -> OrientationEstimate:
        raise RuntimeError("Simulated native backend failure")


class _LowConfidenceBackend:
    @property
    def name(self) -> str:
        return "low_conf_mock"

    def is_available(self) -> bool:
        return True

    def estimate(self, image: np.ndarray, config: OrientationConfig) -> OrientationEstimate:
        return OrientationEstimate(
            rotation_cw_deg=180,
            confidence=0.12,
            backend_name=self.name,
            scores={0: 0.45, 90: 0.1, 180: 0.55, 270: 0.1},
            applied=True,
        )


def test_corrector_fallback_manual_override_disabled_and_bytes_input() -> None:
    upright = _render_english_document()
    rotated_90 = rotate_image_cw(upright, 90)  # Needs 270° CW to restore

    # 1) Failing primary backend falls back seamlessly to heuristic backend.
    corrector = AutoOrientationCorrector(
        config=OrientationConfig(min_confidence=0.2),
        backends=[_FailingBackend(), HeuristicOrientationBackend()],
    )
    png_bytes = encode_image_bytes(rotated_90, fmt="PNG")
    res = corrector.correct(png_bytes)
    assert res.estimate.backend_name == "heuristic_opencv"
    assert res.rotation_cw_deg == 270
    assert np.array_equal(res.image, upright)

    # 2) Low-confidence estimate below threshold keeps 0° safely.
    gated = AutoOrientationCorrector(
        config=OrientationConfig(min_confidence=0.5),
        backends=[_LowConfidenceBackend()],
    )
    gated_res = gated.correct(rotated_90)
    assert gated_res.estimate.applied is False
    assert gated_res.rotation_cw_deg == 0

    # 3) Manual 90° override takes precedence even when auto-detection is disabled.
    disabled = AutoOrientationCorrector(
        config=OrientationConfig(enabled=False),
        backends=[HeuristicOrientationBackend()],
    )
    assert disabled.detect_orientation(rotated_90).applied is False
    manual_res = disabled.correct(rotated_90, manual_rotation_cw_deg=270)
    assert manual_res.estimate.backend_name == "manual"
    assert manual_res.rotation_cw_deg == 270
    assert np.array_equal(manual_res.image, upright)
