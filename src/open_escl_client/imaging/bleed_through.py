"""Show-through / bleed-through removal for thin double-sided scanned documents."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True, slots=True)
class BleedThroughConfig:
    """Parameters controlling single-page show-through / bleed-through removal."""

    enabled: bool = False
    strength: float = 0.6
    whiten_background: bool = False
    max_bleed_l_drop: float = 78.0
    chroma_protection_threshold: float = 24.0


@dataclass(frozen=True, slots=True)
class BleedThroughResult:
    """Output image and diagnostic metadata from `BleedThroughRemover`."""

    image: np.ndarray
    paper_base_bgr: tuple[int, int, int]
    paper_base_l: float
    bleed_pixel_ratio: float
    applied: bool


class BleedThroughRemover:
    """Suppresses faint reverse-side bleed-through while preserving foreground ink & art."""

    def __init__(self, config: BleedThroughConfig | None = None) -> None:
        self.config = config or BleedThroughConfig()

    @staticmethod
    def estimate_paper_base_lab(lab: np.ndarray) -> tuple[float, float, float]:
        """Estimate paper base color `(L, a, b)` from bright low-chroma pixels."""
        l_ch = lab[:, :, 0].astype(np.float32)
        a_ch = lab[:, :, 1].astype(np.float32)
        b_ch = lab[:, :, 2].astype(np.float32)

        l_cutoff = float(np.percentile(l_ch, 70))
        bright_mask = l_ch >= l_cutoff
        chroma = np.hypot(a_ch - 128.0, b_ch - 128.0)
        neutral_bright = bright_mask & (chroma <= 22.0)

        if int(np.count_nonzero(neutral_bright)) >= 64:
            sel = neutral_bright
        else:
            sel = l_ch >= float(np.percentile(l_ch, 80))

        paper_l = float(np.median(l_ch[sel]))
        paper_a = float(np.median(a_ch[sel]))
        paper_b = float(np.median(b_ch[sel]))
        return paper_l, paper_a, paper_b

    def apply(
        self,
        bgr: np.ndarray,
        config: BleedThroughConfig | None = None,
    ) -> BleedThroughResult:
        """Apply bleed-through reduction to a BGR image and return `BleedThroughResult`."""
        if bgr.size == 0 or bgr.ndim != 3 or bgr.shape[2] != 3:
            raise ValueError("Expected a non-empty 3-channel BGR image")

        cfg = config or self.config
        strength = max(0.0, min(1.0, float(cfg.strength)))

        lab_u8 = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
        paper_l, paper_a, paper_b = self.estimate_paper_base_lab(lab_u8)
        base_lab_pixel = np.array(
            [[[
                int(round(np.clip(paper_l, 0.0, 255.0))),
                int(round(np.clip(paper_a, 0.0, 255.0))),
                int(round(np.clip(paper_b, 0.0, 255.0))),
            ]]],
            dtype=np.uint8,
        )
        base_bgr_px = cv2.cvtColor(base_lab_pixel, cv2.COLOR_LAB2BGR)[0, 0]
        paper_base_bgr = (int(base_bgr_px[0]), int(base_bgr_px[1]), int(base_bgr_px[2]))

        if not cfg.enabled or (strength <= 1e-4 and not cfg.whiten_background):
            return BleedThroughResult(
                image=bgr.copy(),
                paper_base_bgr=paper_base_bgr,
                paper_base_l=paper_l,
                bleed_pixel_ratio=0.0,
                applied=False,
            )

        l_ch = lab_u8[:, :, 0].astype(np.float32)
        a_ch = lab_u8[:, :, 1].astype(np.float32)
        b_ch = lab_u8[:, :, 2].astype(np.float32)

        l_drop = np.clip(paper_l - l_ch, 0.0, 255.0)
        cutoff_drop = 20.0 + strength * max(10.0, float(cfg.max_bleed_l_drop) - 20.0)
        soft_start = cutoff_drop * 0.52
        soft_span = max(8.0, cutoff_drop - soft_start)

        # Smooth tone weight: 1.0 in background/faint bleed-through band, dropping to 0.0 for
        # genuine dark foreground text/line-art.
        tone_weight = 1.0 - np.clip((l_drop - soft_start) / soft_span, 0.0, 1.0)

        # Protect saturated color illustrations/photos from being washed out.
        chroma = np.hypot(a_ch - 128.0, b_ch - 128.0)
        chroma_thr = max(8.0, float(cfg.chroma_protection_threshold))
        chroma_guard = 1.0 - np.clip((chroma - chroma_thr * 0.55) / (chroma_thr * 0.75), 0.0, 1.0)

        # Protect 1px anti-aliased fringes immediately adjacent to dark foreground ink.
        dark_ink_mask = (l_drop >= cutoff_drop).astype(np.uint8) * 255
        near_ink = cv2.dilate(dark_ink_mask, np.ones((3, 3), dtype=np.uint8), iterations=1)
        fringe_guard = np.where((near_ink > 0) & (l_drop >= 14.0), 0.2, 1.0).astype(np.float32)

        raw_weight = tone_weight * chroma_guard * fringe_guard
        raw_weight = cv2.GaussianBlur(raw_weight, (3, 3), 0)

        bleed_gain = 0.25 + 0.75 * strength
        blend = np.clip(raw_weight * bleed_gain, 0.0, 1.0)

        if cfg.whiten_background:
            target_l = max(paper_l, 252.0)
            target_a = 128.0
            target_b = 128.0
            # Pull neutral bright paper background up cleanly when whitening is enabled.
            bg_mask = (l_drop <= 12.0) & (chroma <= 18.0)
            blend = np.where(bg_mask, np.maximum(blend, 0.85), blend)
        else:
            target_l = paper_l
            target_a = paper_a
            target_b = paper_b
            # Do not darken pixels that are already brighter than the estimated paper base.
            blend = np.where(l_ch >= paper_l, 0.0, blend)

        out_l = np.clip(l_ch * (1.0 - blend) + target_l * blend, 0.0, 255.0)
        out_a = np.clip(a_ch * (1.0 - blend) + target_a * blend, 0.0, 255.0)
        out_b = np.clip(b_ch * (1.0 - blend) + target_b * blend, 0.0, 255.0)

        out_lab = np.stack([out_l, out_a, out_b], axis=2).astype(np.uint8)
        out_bgr = cv2.cvtColor(out_lab, cv2.COLOR_LAB2BGR)

        affected = (blend >= 0.15) & (l_drop >= 4.0)
        bleed_ratio = float(np.count_nonzero(affected)) / float(l_ch.size)

        return BleedThroughResult(
            image=out_bgr,
            paper_base_bgr=paper_base_bgr,
            paper_base_l=paper_l,
            bleed_pixel_ratio=bleed_ratio,
            applied=True,
        )
