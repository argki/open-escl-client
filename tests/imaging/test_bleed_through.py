"""Unit tests for single-page bleed-through / show-through removal."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from open_escl_client.imaging.bleed_through import (
    BleedThroughConfig,
    BleedThroughRemover,
)


def _make_synthetic_bleed_through_page(
    *,
    paper_bgr: tuple[int, int, int] = (244, 246, 248),
    bleed_bgr: tuple[int, int, int] = (206, 208, 210),
) -> np.ndarray:
    """Create a synthetic thin-paper scan with foreground text, color art, and faint bleed."""
    img = np.full((600, 500, 3), paper_bgr, dtype=np.uint8)

    # Faint reverse-side show-through blocks & text-like bars across the background
    for y in range(60, 540, 48):
        cv2.rectangle(img, (40, y), (460, y + 14), bleed_bgr, -1)
    cv2.putText(
        img,
        "REVERSE BLEED",
        (60, 290),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.1,
        bleed_bgr,
        3,
        cv2.LINE_AA,
    )

    # Crisp dark foreground text on the front side
    cv2.putText(
        img,
        "FRONT TEXT",
        (55, 160),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.3,
        (28, 28, 28),
        3,
        cv2.LINE_AA,
    )
    cv2.rectangle(img, (55, 195), (430, 208), (32, 32, 32), -1)

    # Saturated color illustration block on the front side
    cv2.rectangle(img, (80, 360), (220, 480), (210, 95, 45), -1)
    cv2.rectangle(img, (250, 360), (410, 480), (60, 140, 225), -1)
    return img


def test_disabled_bleed_through_returns_unchanged_copy() -> None:
    img = _make_synthetic_bleed_through_page()
    remover = BleedThroughRemover(BleedThroughConfig(enabled=False, strength=0.8))
    res = remover.apply(img)

    assert not res.applied
    assert res.bleed_pixel_ratio == 0.0
    assert np.array_equal(res.image, img)


def test_bleed_through_reduces_faint_reverse_ink_while_preserving_foreground() -> None:
    paper = (244, 246, 248)
    bleed = (202, 204, 206)
    img = _make_synthetic_bleed_through_page(paper_bgr=paper, bleed_bgr=bleed)

    remover = BleedThroughRemover()
    res = remover.apply(
        img,
        BleedThroughConfig(enabled=True, strength=0.85, whiten_background=False),
    )

    assert res.applied
    assert res.bleed_pixel_ratio > 0.05

    # Bleed-through bar region (e.g. y=62..72, x=100..400) should move close to paper base.
    orig_bleed_patch = img[62:72, 100:400].astype(np.float32)
    clean_bleed_patch = res.image[62:72, 100:400].astype(np.float32)
    assert float(np.mean(clean_bleed_patch)) > float(np.mean(orig_bleed_patch)) + 20.0
    assert abs(float(np.mean(clean_bleed_patch)) - 246.0) <= 12.0

    # Foreground dark bar (y=197..206, x=80..400) must stay crisp and dark.
    fg_bar = res.image[197:206, 80:400]
    assert float(np.mean(fg_bar)) < 45.0

    # Saturated color blocks must retain strong chroma and original hues.
    blue_block = res.image[380:460, 100:200]
    orange_block = res.image[380:460, 270:390]
    assert float(np.mean(blue_block[:, :, 0])) > float(np.mean(blue_block[:, :, 2])) + 100.0
    assert float(np.mean(orange_block[:, :, 2])) > float(np.mean(orange_block[:, :, 0])) + 100.0


def test_whiten_background_option_controls_paper_tint() -> None:
    cream_paper = (224, 240, 246)
    cream_bleed = (188, 204, 212)
    img = _make_synthetic_bleed_through_page(paper_bgr=cream_paper, bleed_bgr=cream_bleed)

    remover = BleedThroughRemover()
    res_preserve = remover.apply(
        img,
        BleedThroughConfig(enabled=True, strength=0.7, whiten_background=False),
    )
    res_whiten = remover.apply(
        img,
        BleedThroughConfig(enabled=True, strength=0.7, whiten_background=True),
    )

    # Pure paper area without bleed (e.g. y=10..35, x=20..200)
    orig_bg = img[10:35, 20:200]
    pres_bg = res_preserve.image[10:35, 20:200]
    white_bg = res_whiten.image[10:35, 20:200]

    # Preserved paper retains its warm tint (R > B); whitened paper approaches neutral bright white.
    assert float(np.mean(pres_bg[:, :, 2]) - np.mean(pres_bg[:, :, 0])) >= 14.0
    assert abs(float(np.mean(pres_bg)) - float(np.mean(orig_bg))) <= 4.0
    assert float(np.mean(white_bg)) >= 248.0


def test_strength_parameter_scales_removal_intensity() -> None:
    img = _make_synthetic_bleed_through_page(
        paper_bgr=(246, 246, 246), bleed_bgr=(202, 202, 202)
    )
    remover = BleedThroughRemover()

    res_low = remover.apply(img, BleedThroughConfig(enabled=True, strength=0.2))
    res_high = remover.apply(img, BleedThroughConfig(enabled=True, strength=0.9))

    low_bleed_mean = float(np.mean(res_low.image[62:72, 100:400]))
    high_bleed_mean = float(np.mean(res_high.image[62:72, 100:400]))
    assert high_bleed_mean > low_bleed_mean + 8.0


def test_invalid_image_raises_value_error() -> None:
    remover = BleedThroughRemover()
    with pytest.raises(ValueError):
        remover.apply(np.zeros((10, 10), dtype=np.uint8))
