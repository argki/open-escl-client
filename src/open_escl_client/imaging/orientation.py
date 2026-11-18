"""Automatic 0° / 90° / 180° / 270° document orientation detection and rotation.

Determines the upright orientation of a scanned document based on its content
(text orientation, reading layout, and typography cues) and applies lossless
90-degree-increment rotation.

Architecture:
- `OrientationDetectorBackend`: Pluggable interface isolating OS-specific engines
  from cross-platform fallbacks.
- `MacOSVisionOrientationBackend`: Primary macOS backend leveraging Apple's native
  Vision framework (`VNRecognizeTextRequest`) for high-accuracy Japanese/English
  orientation recognition.
- `HeuristicOrientationBackend`: Zero-dependency cross-platform fallback (OpenCV /
  NumPy) that works on Windows, Linux, and macOS environments without native OCR.
- `AutoOrientationCorrector`: Orchestrates backend selection, confidence gating
  (preventing false rotations on photos or blank pages), manual override, and
  lossless rotation.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import cv2
import numpy as np

from open_escl_client.imaging.multi_crop import (
    RotationDeg,
    decode_image_bytes,
    normalize_rotation_cw,
)

VALID_ROTATIONS: tuple[RotationDeg, ...] = (0, 90, 180, 270)


def rotate_image_cw(image: np.ndarray, rotation_cw_deg: int) -> np.ndarray:
    """Apply lossless clockwise rotation in 90-degree increments."""
    if image is None or image.size == 0:
        raise ValueError("Input image must not be empty")

    deg = normalize_rotation_cw(rotation_cw_deg)
    if deg == 0:
        return image.copy()
    if deg == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if deg == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)


def _ensure_bgr(image: np.ndarray) -> np.ndarray:
    if image is None or image.size == 0:
        raise ValueError("Input image must not be empty")
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.ndim == 3 and image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    if image.ndim == 3 and image.shape[2] == 3:
        return image
    raise ValueError(f"Unsupported image shape: {image.shape}")


def _downsample_for_analysis(image: np.ndarray, max_dimension: int) -> np.ndarray:
    h, w = image.shape[:2]
    max_side = max(h, w)
    if max_dimension <= 0 or max_side <= max_dimension:
        return image
    scale = max_dimension / float(max_side)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    return cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)


@dataclass(frozen=True, slots=True)
class OrientationEstimate:
    """Estimated clockwise rotation needed to make a scanned document upright."""

    rotation_cw_deg: RotationDeg
    confidence: float
    backend_name: str
    scores: dict[int, float]
    applied: bool = True


@dataclass(frozen=True, slots=True)
class OrientationConfig:
    """Configuration for automatic 0° / 90° / 180° / 270° orientation correction."""

    enabled: bool = True
    min_confidence: float = 0.25
    prefer_native_backend: bool = True
    max_analysis_dimension: int = 1200
    recognition_languages: tuple[str, ...] = ("ja-JP", "en-US")
    vision_timeout_sec: float = 15.0


@dataclass(frozen=True, slots=True)
class OrientationResult:
    """Result of orientation detection and (optional) lossless rotation."""

    image: np.ndarray
    rotation_cw_deg: RotationDeg
    estimate: OrientationEstimate


@runtime_checkable
class OrientationDetectorBackend(Protocol):
    """Interface implemented by OS-specific and cross-platform orientation backends."""

    @property
    def name(self) -> str:
        """Human-readable identifier for this backend."""
        ...

    def is_available(self) -> bool:
        """Return True if this backend can execute in the current environment."""
        ...

    def estimate(
        self,
        image: np.ndarray,
        config: OrientationConfig,
    ) -> OrientationEstimate:
        """Estimate the clockwise rotation (0/90/180/270) required to upright `image`."""
        ...


def _scores_to_estimate(
    scores: dict[int, float],
    *,
    backend_name: str,
    min_best_score: float,
    margin_scale: float = 0.55,
) -> OrientationEstimate:
    """Convert raw 4-way orientation scores into a calibrated `OrientationEstimate`."""
    normalized_scores = {deg: float(max(0.0, scores.get(deg, 0.0))) for deg in VALID_ROTATIONS}
    ranked = sorted(normalized_scores.items(), key=lambda kv: kv[1], reverse=True)
    best_deg, best_score = ranked[0]
    second_score = ranked[1][1]
    total_score = sum(normalized_scores.values())

    if best_score < min_best_score or total_score <= 0.0:
        return OrientationEstimate(
            rotation_cw_deg=0,
            confidence=0.0,
            backend_name=backend_name,
            scores=normalized_scores,
            applied=False,
        )

    rel_margin = (best_score - second_score) / max(best_score, 1e-6)
    share = best_score / max(total_score, 1e-6)
    # Combine winner margin over runner-up and overall dominance over the 4 candidates.
    confidence = min(1.0, max(0.0, (rel_margin / max(margin_scale, 1e-6)) * (0.4 + 0.6 * share)))

    return OrientationEstimate(
        rotation_cw_deg=normalize_rotation_cw(best_deg),
        confidence=round(float(confidence), 4),
        backend_name=backend_name,
        scores={k: round(v, 4) for k, v in normalized_scores.items()},
        applied=True,
    )


# ---------------------------------------------------------------------------
# macOS native Vision framework backend
# ---------------------------------------------------------------------------

_SWIFT_VISION_HELPER_SOURCE = r"""
import Foundation
import CoreGraphics
import ImageIO
import Vision

func loadCGImage(at path: String) -> CGImage? {
    let url = URL(fileURLWithPath: path)
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil) else {
        return nil
    }
    return CGImageSourceCreateImageAtIndex(src, 0, nil)
}

let args = CommandLine.arguments
guard args.count >= 2 else {
    fputs("Usage: vision_orientation_helper <image_path> [lang1,lang2]\n", stderr)
    exit(2)
}

let imagePath = args[1]
let languages: [String]
if args.count >= 3 && !args[2].isEmpty {
    languages = args[2].split(separator: ",").map { String($0) }
} else {
    languages = ["ja-JP", "en-US"]
}

guard let cgImage = loadCGImage(at: imagePath) else {
    fputs("Failed to load image\n", stderr)
    exit(1)
}

let imgW = Double(cgImage.width)
let imgH = Double(cgImage.height)

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
if !languages.isEmpty {
    request.recognitionLanguages = languages
}

// Single-pass recognition: Vision returns the true character-up orientation in
// each observation's quadrilateral (topLeft/topRight/bottomLeft/bottomRight),
// even when text is rotated 90°, 180°, or 270°.
let handler = VNImageRequestHandler(
    cgImage: cgImage,
    orientation: .up,
    options: [:]
)
do {
    try handler.perform([request])
} catch {
    fputs("Vision request failed: \(error)\n", stderr)
    exit(1)
}

var scores: [String: Double] = [
    "0": 0.0,
    "90": 0.0,
    "180": 0.0,
    "270": 0.0,
]

let alnum = CharacterSet.alphanumerics
for obs in request.results ?? [] {
    guard let candidate = obs.topCandidates(1).first else { continue }
    let conf = Double(candidate.confidence)
    if conf < 0.25 { continue }

    let text = candidate.string.trimmingCharacters(in: .whitespacesAndNewlines)
    if text.isEmpty { continue }

    var meaningfulCount: Double = 0.0
    for scalar in text.unicodeScalars {
        let v = scalar.value
        let isCJK = (0x3040...0x30FF).contains(v)
            || (0x4E00...0x9FFF).contains(v)
            || (0x3400...0x4DBF).contains(v)
            || (0xFF01...0xFF60).contains(v)
        if isCJK || alnum.contains(scalar) {
            meaningfulCount += 1.0
        }
    }
    if meaningfulCount < 1.0 { continue }

    // Vision normalized coordinates have origin at bottom-left (+X right, +Y up).
    // Compute the pixel-scaled character "up" vector (bottom -> top) averaged across
    // left and right edges of the text observation.
    let ux = ((Double(obs.topLeft.x - obs.bottomLeft.x)
        + Double(obs.topRight.x - obs.bottomRight.x)) * 0.5) * imgW
    let uy = ((Double(obs.topLeft.y - obs.bottomLeft.y)
        + Double(obs.topRight.y - obs.bottomRight.y)) * 0.5) * imgH

    let upLen = hypot(ux, uy)
    if upLen < 1e-3 { continue }

    // Determine which CW rotation makes the character "up" vector point to +Y (upright):
    //   up ~ +Y (uy >= |ux|)  -> already upright (0° CW)
    //   up ~ -X (-ux >= |uy|) -> head points left, rotate 90° CW
    //   up ~ -Y (-uy >= |ux|) -> upside down, rotate 180° CW
    //   up ~ +X (ux >= |uy|)  -> head points right, rotate 270° CW (90° CCW)
    let targetDeg: String
    if abs(uy) >= abs(ux) {
        targetDeg = uy >= 0.0 ? "0" : "180"
    } else {
        targetDeg = ux < 0.0 ? "90" : "270"
    }

    let lineBonus = meaningfulCount >= 2.0 ? 1.0 : 0.35
    let weight = meaningfulCount * conf * conf * lineBonus
    scores[targetDeg, default: 0.0] += weight
}

if let data = try? JSONSerialization.data(withJSONObject: scores, options: []),
   let json = String(data: data, encoding: .utf8) {
    print(json)
} else {
    exit(1)
}
"""


class MacOSVisionOrientationBackend:
    """macOS native orientation detector backed by Apple's Vision framework.

    Compiles a tiny Swift helper binary once into a local cache directory and
    reuses it across calls for fast, Neural-Engine-accelerated Japanese and English
    text orientation scoring with zero extra Python package dependencies.
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir
        self._compiled_binary: Path | None = None
        self._compile_attempted = False

    @property
    def name(self) -> str:
        return "macos_vision"

    def is_available(self) -> bool:
        if platform.system() != "Darwin":
            return False
        return shutil.which("swiftc") is not None

    def _resolve_cache_dir(self) -> Path:
        if self._cache_dir is not None:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            return self._cache_dir

        candidates = [
            Path.home() / "Library" / "Caches" / "open-escl-client" / "orientation",
            Path(tempfile.gettempdir()) / "open-escl-client" / "orientation",
        ]
        for candidate in candidates:
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                probe = candidate / ".write_probe"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
                return candidate
            except OSError:
                continue
        return Path(tempfile.gettempdir())

    def _ensure_helper_binary(self) -> Path | None:
        if self._compiled_binary is not None and self._compiled_binary.exists():
            return self._compiled_binary
        if self._compile_attempted:
            return None

        self._compile_attempted = True
        swiftc = shutil.which("swiftc")
        if swiftc is None:
            return None

        src_hash = hashlib.sha256(_SWIFT_VISION_HELPER_SOURCE.encode("utf-8")).hexdigest()[:12]
        cache_dir = self._resolve_cache_dir()
        bin_path = cache_dir / f"vision_orientation_helper_{src_hash}"
        if bin_path.exists() and os.access(bin_path, os.X_OK):
            self._compiled_binary = bin_path
            return bin_path

        src_path = cache_dir / f"vision_orientation_helper_{src_hash}.swift"
        mod_cache = cache_dir / "module_cache"
        mod_cache.mkdir(parents=True, exist_ok=True)
        tmp_bin = cache_dir / f".vision_orientation_helper_{src_hash}.{os.getpid()}.tmp"
        try:
            src_path.write_text(_SWIFT_VISION_HELPER_SOURCE, encoding="utf-8")
            proc = subprocess.run(
                [
                    swiftc,
                    "-O",
                    "-module-cache-path",
                    str(mod_cache),
                    str(src_path),
                    "-o",
                    str(tmp_bin),
                ],
                capture_output=True,
                text=True,
                timeout=45.0,
                check=False,
            )
            if proc.returncode != 0 or not tmp_bin.exists():
                tmp_bin.unlink(missing_ok=True)
                return None
            tmp_bin.replace(bin_path)
            self._compiled_binary = bin_path
            return bin_path
        except (OSError, subprocess.SubprocessError):
            tmp_bin.unlink(missing_ok=True)
            return None

    def estimate(
        self,
        image: np.ndarray,
        config: OrientationConfig,
    ) -> OrientationEstimate:
        helper = self._ensure_helper_binary()
        if helper is None:
            raise RuntimeError("macOS Vision helper binary could not be prepared")

        work = _downsample_for_analysis(_ensure_bgr(image), config.max_analysis_dimension)
        lang_arg = ",".join(config.recognition_languages)

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                tmp_path = Path(tf.name)
            ok = cv2.imwrite(str(tmp_path), work)
            if not ok:
                raise RuntimeError("Failed to write temporary image for Vision analysis")

            proc = subprocess.run(
                [str(helper), str(tmp_path), lang_arg],
                capture_output=True,
                text=True,
                timeout=config.vision_timeout_sec,
                check=False,
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                err_msg = proc.stderr.strip()
                raise RuntimeError(
                    f"Vision orientation helper failed (rc={proc.returncode}): {err_msg}"
                )

            raw = json.loads(proc.stdout.strip())
            scores = {deg: float(raw.get(str(deg), 0.0)) for deg in VALID_ROTATIONS}
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

        return _scores_to_estimate(
            scores,
            backend_name=self.name,
            min_best_score=2.0,
            margin_scale=0.45,
        )


# ---------------------------------------------------------------------------
# Cross-platform OpenCV / NumPy heuristic fallback backend
# ---------------------------------------------------------------------------


class HeuristicOrientationBackend:
    """Cross-platform orientation estimator using projection and layout typography cues.

    Requires only OpenCV and NumPy (works on Windows, Linux, and macOS).
    Evaluates:
    1. Text-line axis sharpness (horizontal vs vertical projection energy) to
       distinguish `{0°, 180°}` from `{90°, 270°}`.
    2. Paragraph left-alignment consistency, short-line left bias, upper-page weight,
       and intra-line upper-half stroke density to distinguish upright (`0°`) from
       upside-down (`180°`).
    """

    @property
    def name(self) -> str:
        return "heuristic_opencv"

    def is_available(self) -> bool:
        return True

    def estimate(
        self,
        image: np.ndarray,
        config: OrientationConfig,
    ) -> OrientationEstimate:
        bgr = _downsample_for_analysis(
            _ensure_bgr(image), min(config.max_analysis_dimension, 900)
        )
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        if min(h, w) < 40:
            return OrientationEstimate(
                rotation_cw_deg=0,
                confidence=0.0,
                backend_name=self.name,
                scores={0: 0.0, 90: 0.0, 180: 0.0, 270: 0.0},
                applied=False,
            )

        # Crop inner 90% to ignore page border shadows.
        mx = max(2, int(round(w * 0.05)))
        my = max(2, int(round(h * 0.05)))
        roi = gray[my : h - my, mx : w - mx]

        block = max(11, (min(roi.shape[:2]) // 18) | 1)
        block = min(block, 41)
        binary = cv2.adaptiveThreshold(
            roi,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            block,
            14,
        )
        # Remove isolated 1-pixel noise speckles.
        binary = cv2.morphologyEx(
            binary,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)),
        )

        ink_ratio = float(np.count_nonzero(binary)) / float(binary.size)
        if ink_ratio < 0.004 or ink_ratio > 0.55:
            return OrientationEstimate(
                rotation_cw_deg=0,
                confidence=0.0,
                backend_name=self.name,
                scores={0: 0.0, 90: 0.0, 180: 0.0, 270: 0.0},
                applied=False,
            )

        # Step 1: Measure horizontal vs vertical line periodicity.
        h_axis_score, v_axis_score = self._measure_axis_scores(binary)
        axis_total = h_axis_score + v_axis_score
        if axis_total <= 1e-9:
            return OrientationEstimate(
                rotation_cw_deg=0,
                confidence=0.0,
                backend_name=self.name,
                scores={0: 0.0, 90: 0.0, 180: 0.0, 270: 0.0},
                applied=False,
            )

        axis_dominance = abs(h_axis_score - v_axis_score) / axis_total
        # If there is no clear text-line structure along either axis (e.g., photo/graphic),
        # refuse to guess an arbitrary rotation.
        if axis_dominance < 0.12:
            return OrientationEstimate(
                rotation_cw_deg=0,
                confidence=0.0,
                backend_name=self.name,
                scores={0: 0.0, 90: 0.0, 180: 0.0, 270: 0.0},
                applied=False,
            )

        # Step 2: Score all 4 clockwise rotations.
        scores: dict[int, float] = {}
        for deg in VALID_ROTATIONS:
            rot_bin = rotate_image_cw(binary, deg)
            axis_weight = (
                (h_axis_score / axis_total)
                if deg in (0, 180)
                else (v_axis_score / axis_total)
            )
            upright_cue = self._score_upright_layout(rot_bin)
            scores[deg] = axis_weight * upright_cue

        return _scores_to_estimate(
            scores,
            backend_name=self.name,
            min_best_score=0.25,
            margin_scale=0.18,
        )

    @staticmethod
    def _measure_axis_scores(binary: np.ndarray) -> tuple[float, float]:
        norm = binary.astype(np.float32) * (1.0 / 255.0)
        h_proj = np.mean(norm, axis=1)
        v_proj = np.mean(norm, axis=0)

        h_energy = float(np.var(h_proj)) + float(np.mean(np.diff(h_proj) ** 2)) * 2.5
        v_energy = float(np.var(v_proj)) + float(np.mean(np.diff(v_proj) ** 2)) * 2.5
        return h_energy, v_energy

    @staticmethod
    def _score_upright_layout(binary: np.ndarray) -> float:
        """Score how consistent `binary` is with an upright horizontal-reading document."""
        h, w = binary.shape[:2]
        norm = binary.astype(np.float32) * (1.0 / 255.0)
        row_density = np.mean(norm, axis=1)

        peak = float(np.max(row_density))
        if peak <= 1e-4:
            return 0.0

        # Segment horizontal text-line bands.
        thresh = max(0.008, peak * 0.14)
        active = row_density >= thresh
        bands: list[tuple[int, int]] = []
        in_band = False
        start = 0
        for y, flag in enumerate(active):
            if flag and not in_band:
                start = y
                in_band = True
            elif not flag and in_band:
                if y - start >= 3:
                    bands.append((start, y))
                in_band = False
        if in_band and (h - start) >= 3:
            bands.append((start, h))

        if len(bands) < 2:
            return 0.2

        left_edges: list[float] = []
        right_margins: list[float] = []
        line_widths: list[float] = []
        short_line_left_bias: list[float] = []
        upper_half_bias: list[float] = []

        for y0, y1 in bands:
            band_slice = norm[y0:y1, :]
            col_density = np.mean(band_slice, axis=0)
            cols = np.flatnonzero(col_density > 0.02)
            if len(cols) < 4:
                continue
            lx = float(cols[0]) / float(w)
            rx = float(cols[-1]) / float(w)
            rm = 1.0 - rx
            lw = rx - lx
            if lw < 0.03:
                continue

            left_edges.append(lx)
            right_margins.append(rm)
            line_widths.append(lw)

            # Intra-line upper-half vs lower-half ink density (ascenders & capitals).
            bh = y1 - y0
            if bh >= 6:
                mid = bh // 2
                upper = float(np.mean(band_slice[:mid, :]))
                lower = float(np.mean(band_slice[bh - mid :, :]))
                denom = upper + lower
                if denom > 1e-5:
                    upper_half_bias.append((upper - lower) / denom)

        if len(left_edges) < 2:
            return 0.2

        max_lw = max(line_widths)
        for lx, rm, lw in zip(left_edges, right_margins, line_widths, strict=True):
            # Shorter lines (paragraph ends, headings, bullet items) sit on the left in
            # upright LTR documents and on the right when upside down.
            if lw < max_lw * 0.82:
                short_line_left_bias.append(rm - lx)

        # Cue 1: Left-edge alignment stability vs right-edge jaggedness.
        left_std = float(np.std(left_edges))
        right_std = float(np.std(right_margins))
        align_denom = left_std + right_std + 1e-4
        left_align_cue = (right_std - left_std) / align_denom  # in [-1, 1]

        # Cue 2: Short lines biased toward the left margin.
        if short_line_left_bias:
            short_cue = float(np.clip(np.mean(short_line_left_bias) * 2.5, -1.0, 1.0))
        else:
            margin_diff = float(np.mean(right_margins)) - float(np.mean(left_edges))
            short_cue = float(np.clip(margin_diff * 2.0, -1.0, 1.0))

        # Cue 3: Upper-page content weight (documents commonly leave more blank margin at bottom).
        ys = np.flatnonzero(active)
        top_margin = float(ys[0]) / float(h)
        bottom_margin = float(h - 1 - ys[-1]) / float(h)
        top_cue = float(np.clip((bottom_margin - top_margin) * 2.0, -1.0, 1.0))

        # Cue 4: Ascender / upper-half character stroke bias.
        ascender_cue = (
            float(np.clip(np.mean(upper_half_bias) * 2.0, -1.0, 1.0))
            if upper_half_bias
            else 0.0
        )

        # Map weighted sum in [-1, 1] into strictly positive score [0.2, 2.0].
        combined = (
            0.40 * left_align_cue
            + 0.35 * short_cue
            + 0.15 * top_cue
            + 0.10 * ascender_cue
        )
        return float(max(0.1, 1.0 + combined))


# ---------------------------------------------------------------------------
# High-level AutoOrientationCorrector orchestrator
# ---------------------------------------------------------------------------


class AutoOrientationCorrector:
    """Detects and corrects 90° / 180° / 270° document orientation automatically."""

    def __init__(
        self,
        config: OrientationConfig | None = None,
        backends: list[OrientationDetectorBackend] | None = None,
    ) -> None:
        self.config = config or OrientationConfig()
        if backends is not None:
            self._backends = list(backends)
        else:
            self._backends = self._default_backends(self.config)

    @staticmethod
    def _default_backends(config: OrientationConfig) -> list[OrientationDetectorBackend]:
        backends: list[OrientationDetectorBackend] = []
        if config.prefer_native_backend:
            vision = MacOSVisionOrientationBackend()
            if vision.is_available():
                backends.append(vision)
        backends.append(HeuristicOrientationBackend())
        return backends

    @property
    def backends(self) -> tuple[OrientationDetectorBackend, ...]:
        return tuple(self._backends)

    def detect_orientation(self, image: np.ndarray | bytes) -> OrientationEstimate:
        """Estimate the clockwise rotation (`0`, `90`, `180`, `270`) needed to upright `image`."""
        bgr = _ensure_bgr(decode_image_bytes(image) if isinstance(image, bytes) else image)
        if not self.config.enabled:
            return OrientationEstimate(
                rotation_cw_deg=0,
                confidence=0.0,
                backend_name="disabled",
                scores={0: 0.0, 90: 0.0, 180: 0.0, 270: 0.0},
                applied=False,
            )

        last_estimate: OrientationEstimate | None = None
        for backend in self._backends:
            if not backend.is_available():
                continue
            try:
                estimate = backend.estimate(bgr, self.config)
            except Exception:
                continue

            last_estimate = estimate
            if estimate.confidence >= self.config.min_confidence:
                return OrientationEstimate(
                    rotation_cw_deg=estimate.rotation_cw_deg,
                    confidence=estimate.confidence,
                    backend_name=estimate.backend_name,
                    scores=estimate.scores,
                    applied=True,
                )

            # Native OCR backend succeeded and found no reliable text (e.g. a photo):
            # do not let a heuristic guess override an explicit low-confidence native result
            # unless the native backend produced completely zero scores.
            if backend.name == "macos_vision" and sum(estimate.scores.values()) > 0.0:
                return OrientationEstimate(
                    rotation_cw_deg=0,
                    confidence=estimate.confidence,
                    backend_name=estimate.backend_name,
                    scores=estimate.scores,
                    applied=False,
                )

        if last_estimate is not None:
            return OrientationEstimate(
                rotation_cw_deg=0,
                confidence=last_estimate.confidence,
                backend_name=last_estimate.backend_name,
                scores=last_estimate.scores,
                applied=False,
            )

        return OrientationEstimate(
            rotation_cw_deg=0,
            confidence=0.0,
            backend_name="none",
            scores={0: 0.0, 90: 0.0, 180: 0.0, 270: 0.0},
            applied=False,
        )

    def correct(
        self,
        image: np.ndarray | bytes,
        *,
        manual_rotation_cw_deg: int | None = None,
    ) -> OrientationResult:
        """Rotate `image` upright using either a manual 90° step override or auto detection."""
        bgr = _ensure_bgr(decode_image_bytes(image) if isinstance(image, bytes) else image)

        if manual_rotation_cw_deg is not None:
            rot = normalize_rotation_cw(manual_rotation_cw_deg)
            rotated = rotate_image_cw(bgr, rot)
            estimate = OrientationEstimate(
                rotation_cw_deg=rot,
                confidence=1.0,
                backend_name="manual",
                scores={d: (1.0 if d == rot else 0.0) for d in VALID_ROTATIONS},
                applied=True,
            )
            return OrientationResult(
                image=rotated,
                rotation_cw_deg=rot,
                estimate=estimate,
            )

        estimate = self.detect_orientation(bgr)
        applied_rot: RotationDeg = estimate.rotation_cw_deg if estimate.applied else 0
        rotated = rotate_image_cw(bgr, applied_rot)
        return OrientationResult(
            image=rotated,
            rotation_cw_deg=applied_rot,
            estimate=estimate,
        )
