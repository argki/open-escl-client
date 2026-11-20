"""Cross-platform Japanese OCR abstraction with macOS Vision and ONNX adapter backends."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import platform
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

import cv2
import numpy as np

from open_escl_client.imaging.multi_crop import _ensure_bgr, decode_image_bytes

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OcrBoundingBox:
    """Normalized image bounding box with top-left origin (`[0.0, 1.0]`)."""

    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return min(1.0, self.x + self.width)

    @property
    def bottom(self) -> float:
        return min(1.0, self.y + self.height)

    @property
    def center_x(self) -> float:
        return self.x + self.width * 0.5

    @property
    def center_y(self) -> float:
        return self.y + self.height * 0.5


@dataclass(frozen=True, slots=True)
class OcrTextBlock:
    """Single recognized text block/line with layout coordinates and confidence."""

    text: str
    bbox: OcrBoundingBox
    confidence: float
    stroke_width_norm: float = 0.0
    ink_ratio: float = 0.0

    @property
    def height_norm(self) -> float:
        """Normalized vertical height of this text line in `[0.0, 1.0]`."""
        return float(self.bbox.height)


@dataclass(frozen=True, slots=True)
class OcrConfig:
    """Configuration controlling Japanese/multilingual OCR execution."""

    recognition_languages: tuple[str, ...] = ("ja-JP", "en-US")
    recognition_level: Literal["accurate", "fast"] = "accurate"
    uses_language_correction: bool = True
    min_confidence: float = 0.2
    max_analysis_dimension: int = 2400
    vision_timeout_sec: float = 20.0
    prefer_native_backend: bool = True


@dataclass(frozen=True, slots=True)
class OcrResult:
    """Complete OCR output including ordered blocks and joined full text."""

    blocks: tuple[OcrTextBlock, ...]
    full_text: str
    backend_name: str
    image_width: int
    image_height: int
    applied: bool = True
    error_message: str = ""

    @property
    def has_text(self) -> bool:
        return bool(self.full_text.strip())


@runtime_checkable
class OcrBackend(Protocol):
    """Protocol for pluggable OS-specific or cross-platform OCR backends."""

    @property
    def name(self) -> str: ...

    def is_available(self) -> bool: ...

    def recognize(self, image: np.ndarray, config: OcrConfig) -> OcrResult: ...


def _downsample_for_ocr(bgr: np.ndarray, max_dim: int) -> np.ndarray:
    h, w = bgr.shape[:2]
    longest = max(h, w)
    if max_dim <= 0 or longest <= max_dim:
        return bgr
    scale = float(max_dim) / float(longest)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    return cv2.resize(bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)


def _sort_blocks_reading_order(blocks: Sequence[OcrTextBlock]) -> tuple[OcrTextBlock, ...]:
    """Order text blocks top-to-bottom, grouping horizontal peers left-to-right."""
    if not blocks:
        return ()
    heights = sorted(max(1e-4, b.bbox.height) for b in blocks)
    median_h = heights[len(heights) // 2]
    row_step = max(0.008, median_h * 0.55)
    ordered = sorted(
        blocks,
        key=lambda b: (int(round(b.bbox.center_y / row_step)), b.bbox.x),
    )
    return tuple(ordered)


def _measure_block_visual_metrics(
    gray: np.ndarray,
    bbox: OcrBoundingBox,
) -> tuple[float, float]:
    """Estimate normalized stroke thickness and foreground ink density inside `bbox`."""
    img_h, img_w = gray.shape[:2]
    if img_h <= 0 or img_w <= 0:
        return 0.0, 0.0

    x0 = max(0, min(img_w - 1, int(np.floor(bbox.x * img_w))))
    y0 = max(0, min(img_h - 1, int(np.floor(bbox.y * img_h))))
    x1 = max(x0 + 1, min(img_w, int(np.ceil((bbox.x + bbox.width) * img_w))))
    y1 = max(y0 + 1, min(img_h, int(np.ceil((bbox.y + bbox.height) * img_h))))

    patch = gray[y0:y1, x0:x1]
    ph, pw = patch.shape[:2]
    if ph < 4 or pw < 4:
        return 0.0, 0.0
    if int(patch.max()) - int(patch.min()) < 15:
        return 0.0, 0.0

    _, binary_dark = cv2.threshold(patch, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # Invert foreground when text sits on a dark banner (white/light text on dark background).
    border_pixels = np.concatenate(
        [binary_dark[0, :], binary_dark[-1, :], binary_dark[:, 0], binary_dark[:, -1]]
    )
    dark_border_ratio = (
        float(np.count_nonzero(border_pixels)) / float(border_pixels.size)
        if border_pixels.size > 0
        else 0.0
    )
    if dark_border_ratio > 0.55:
        fg = cv2.bitwise_not(binary_dark)
    else:
        fg = binary_dark

    fg_count = int(np.count_nonzero(fg))
    ink_ratio = float(fg_count) / float(fg.size)
    if ink_ratio < 0.02 or ink_ratio > 0.78:
        return 0.0, ink_ratio

    dist = cv2.distanceTransform(fg, cv2.DIST_L2, 3)
    fg_dists = dist[fg > 0]
    if fg_dists.size == 0:
        return 0.0, ink_ratio

    stroke_px = 2.0 * float(np.percentile(fg_dists, 75))
    max_sane_px = max(1.0, min(float(max(ph, pw)) * 0.5, float(min(ph, pw)) * 0.65))
    stroke_px = min(stroke_px, max_sane_px)
    return stroke_px / float(img_h), ink_ratio


def _enrich_blocks_with_visual_metrics(
    blocks: Sequence[OcrTextBlock],
    bgr_or_gray: np.ndarray | None,
) -> tuple[OcrTextBlock, ...]:
    """Attach stroke width and ink ratio metrics computed from the source image."""
    if not blocks or bgr_or_gray is None:
        return tuple(blocks)

    if any(b.stroke_width_norm > 0.0 for b in blocks):
        return tuple(blocks)

    if bgr_or_gray.ndim == 2:
        gray = bgr_or_gray
    else:
        work = _downsample_for_ocr(bgr_or_gray, 1600)
        gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)

    enriched: list[OcrTextBlock] = []
    for block in blocks:
        stroke_norm, ink_ratio = _measure_block_visual_metrics(gray, block.bbox)
        enriched.append(
            replace(
                block,
                stroke_width_norm=stroke_norm,
                ink_ratio=ink_ratio,
            )
        )
    return tuple(enriched)


def _build_ocr_result(
    raw_blocks: Sequence[OcrTextBlock],
    *,
    backend_name: str,
    image_width: int,
    image_height: int,
    min_confidence: float,
    applied: bool = True,
    source_image: np.ndarray | None = None,
) -> OcrResult:
    filtered = [
        b
        for b in raw_blocks
        if b.text.strip() and float(b.confidence) >= float(min_confidence)
    ]
    ordered = _sort_blocks_reading_order(filtered)
    enriched = _enrich_blocks_with_visual_metrics(ordered, source_image)
    full_text = "\n".join(b.text.strip() for b in enriched)
    return OcrResult(
        blocks=enriched,
        full_text=full_text,
        backend_name=backend_name,
        image_width=int(image_width),
        image_height=int(image_height),
        applied=applied,
    )


# ---------------------------------------------------------------------------
# macOS native Apple Vision framework OCR backend (`ja-JP` accurate mode)
# ---------------------------------------------------------------------------

_SWIFT_OCR_HELPER_SOURCE = r"""
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
    fputs("Usage: vision_ocr_helper <image_path> [langs] [accurate|fast] [true|false]\n", stderr)
    exit(2)
}

let imagePath = args[1]
let languages: [String]
if args.count >= 3 && !args[2].isEmpty {
    languages = args[2].split(separator: ",").map { String($0) }
} else {
    languages = ["ja-JP", "en-US"]
}
let levelArg = args.count >= 4 ? args[3].lowercased() : "accurate"
let correctionArg = args.count >= 5 ? args[4].lowercased() : "true"

guard let cgImage = loadCGImage(at: imagePath) else {
    fputs("Failed to load image\n", stderr)
    exit(1)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = (levelArg == "fast") ? .fast : .accurate
request.usesLanguageCorrection = (correctionArg != "false")
if !languages.isEmpty {
    request.recognitionLanguages = languages
}

let handler = VNImageRequestHandler(
    cgImage: cgImage,
    orientation: .up,
    options: [:]
)
do {
    try handler.perform([request])
} catch {
    fputs("Vision OCR request failed: \(error)\n", stderr)
    exit(1)
}

var items: [[String: Any]] = []
for obs in request.results ?? [] {
    guard let candidate = obs.topCandidates(1).first else { continue }
    let text = candidate.string.trimmingCharacters(in: .whitespacesAndNewlines)
    if text.isEmpty { continue }

    let bb = obs.boundingBox
    let x = max(0.0, min(1.0, Double(bb.origin.x)))
    let w = max(0.0, min(1.0 - x, Double(bb.size.width)))
    let yTop = max(0.0, min(1.0, 1.0 - Double(bb.origin.y + bb.size.height)))
    let h = max(0.0, min(1.0 - yTop, Double(bb.size.height)))

    items.append([
        "text": text,
        "confidence": Double(candidate.confidence),
        "x": x,
        "y": yTop,
        "width": w,
        "height": h,
    ])
}

if let data = try? JSONSerialization.data(withJSONObject: items, options: []),
   let json = String(data: data, encoding: .utf8) {
    print(json)
} else {
    exit(1)
}
"""


class MacOSVisionOcrBackend:
    """macOS native high-accuracy Japanese/English OCR powered by Apple Vision."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir
        self._compiled_binary: Path | None = None
        self._compile_lock = threading.Lock()

    @property
    def name(self) -> str:
        return "macos_vision_ocr"

    def is_available(self) -> bool:
        if platform.system() != "Darwin":
            return False
        return shutil.which("swiftc") is not None

    def _resolve_cache_dir(self) -> Path:
        if self._cache_dir is not None:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            return self._cache_dir

        candidates = [
            Path.home() / "Library" / "Caches" / "open-escl-client" / "ocr",
            Path(tempfile.gettempdir()) / "open-escl-client" / "ocr",
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

        with self._compile_lock:
            if self._compiled_binary is not None and self._compiled_binary.exists():
                return self._compiled_binary

            swiftc = shutil.which("swiftc")
            if swiftc is None:
                return None

            src_hash = hashlib.sha256(_SWIFT_OCR_HELPER_SOURCE.encode("utf-8")).hexdigest()[:12]
            cache_dir = self._resolve_cache_dir()
            bin_path = cache_dir / f"vision_ocr_helper_{src_hash}"
            if bin_path.exists() and os.access(bin_path, os.X_OK):
                self._compiled_binary = bin_path
                return bin_path

            src_path = cache_dir / f"vision_ocr_helper_{src_hash}.swift"
            mod_cache = cache_dir / "module_cache"
            mod_cache.mkdir(parents=True, exist_ok=True)
            tmp_bin = cache_dir / (
                f".vision_ocr_helper_{src_hash}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            try:
                src_path.write_text(_SWIFT_OCR_HELPER_SOURCE, encoding="utf-8")
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
                    timeout=60.0,
                    check=False,
                )
                if proc.returncode != 0 or not tmp_bin.exists():
                    logger.warning(
                        "Failed to compile macOS Vision OCR helper (rc=%s): %s",
                        proc.returncode,
                        proc.stderr.strip(),
                    )
                    tmp_bin.unlink(missing_ok=True)
                    return None
                tmp_bin.replace(bin_path)
                self._compiled_binary = bin_path
                return bin_path
            except (OSError, subprocess.SubprocessError) as exc:
                logger.warning("macOS Vision OCR helper compilation error: %s", exc)
                tmp_bin.unlink(missing_ok=True)
                return None

    def recognize(self, image: np.ndarray, config: OcrConfig) -> OcrResult:
        helper = self._ensure_helper_binary()
        if helper is None:
            raise RuntimeError("macOS Vision OCR helper binary could not be prepared")

        bgr = _ensure_bgr(image)
        orig_h, orig_w = bgr.shape[:2]
        work = _downsample_for_ocr(bgr, config.max_analysis_dimension)
        lang_arg = ",".join(config.recognition_languages)
        level_arg = "fast" if config.recognition_level == "fast" else "accurate"
        corr_arg = "true" if config.uses_language_correction else "false"

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                tmp_path = Path(tf.name)
            if not cv2.imwrite(str(tmp_path), work):
                raise RuntimeError("Failed to write temporary image for Vision OCR")

            proc = subprocess.run(
                [str(helper), str(tmp_path), lang_arg, level_arg, corr_arg],
                capture_output=True,
                text=True,
                timeout=config.vision_timeout_sec,
                check=False,
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                err_msg = proc.stderr.strip()
                raise RuntimeError(f"Vision OCR failed (rc={proc.returncode}): {err_msg}")
            raw_items = json.loads(proc.stdout.strip())
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

        blocks = [
            OcrTextBlock(
                text=str(item.get("text", "")),
                confidence=float(item.get("confidence", 0.0)),
                bbox=OcrBoundingBox(
                    x=float(item.get("x", 0.0)),
                    y=float(item.get("y", 0.0)),
                    width=float(item.get("width", 0.0)),
                    height=float(item.get("height", 0.0)),
                ),
            )
            for item in raw_items
            if isinstance(item, dict)
        ]
        return _build_ocr_result(
            blocks,
            backend_name=self.name,
            image_width=orig_w,
            image_height=orig_h,
            min_confidence=config.min_confidence,
            source_image=work,
        )


# ---------------------------------------------------------------------------
# Cross-platform local ONNX / graceful fallback backends (Windows / Linux)
# ---------------------------------------------------------------------------


class RapidOcrBackend:
    """Optional cross-platform ONNX OCR adapter (`rapidocr_onnxruntime`)."""

    def __init__(self, engine_factory: Callable[[], Any] | None = None) -> None:
        self._engine_factory = engine_factory
        self._engine: Any | None = None

    @property
    def name(self) -> str:
        return "rapidocr_onnx"

    def is_available(self) -> bool:
        if self._engine_factory is not None:
            return True
        return importlib.util.find_spec("rapidocr_onnxruntime") is not None

    def _get_engine(self) -> Any:
        if self._engine is None:
            if self._engine_factory is not None:
                self._engine = self._engine_factory()
            else:
                from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]

                self._engine = RapidOCR()
        return self._engine

    def recognize(self, image: np.ndarray, config: OcrConfig) -> OcrResult:
        bgr = _ensure_bgr(image)
        orig_h, orig_w = bgr.shape[:2]
        work = _downsample_for_ocr(bgr, config.max_analysis_dimension)
        work_h, work_w = work.shape[:2]

        engine = self._get_engine()
        raw_out = engine(work)
        entries = raw_out[0] if isinstance(raw_out, tuple) else raw_out
        if not entries:
            return OcrResult(
                blocks=(),
                full_text="",
                backend_name=self.name,
                image_width=orig_w,
                image_height=orig_h,
                applied=True,
            )

        blocks: list[OcrTextBlock] = []
        inv_w = 1.0 / float(max(1, work_w))
        inv_h = 1.0 / float(max(1, work_h))
        for entry in entries:
            if not isinstance(entry, (list, tuple)) or len(entry) < 3:
                continue
            pts, text, conf = entry[0], entry[1], entry[2]
            xs = [float(pt[0]) * inv_w for pt in pts]
            ys = [float(pt[1]) * inv_h for pt in pts]
            min_x = max(0.0, min(1.0, min(xs)))
            max_x = max(0.0, min(1.0, max(xs)))
            min_y = max(0.0, min(1.0, min(ys)))
            max_y = max(0.0, min(1.0, max(ys)))
            blocks.append(
                OcrTextBlock(
                    text=str(text),
                    confidence=float(conf),
                    bbox=OcrBoundingBox(
                        x=min_x,
                        y=min_y,
                        width=max(0.0, max_x - min_x),
                        height=max(0.0, max_y - min_y),
                    ),
                )
            )
        return _build_ocr_result(
            blocks,
            backend_name=self.name,
            image_width=orig_w,
            image_height=orig_h,
            min_confidence=config.min_confidence,
            source_image=work,
        )


class NoOpOcrBackend:
    """Graceful fallback backend returning empty `OcrResult` when no OCR engine is available."""

    @property
    def name(self) -> str:
        return "noop_fallback"

    def is_available(self) -> bool:
        return True

    def recognize(self, image: np.ndarray, config: OcrConfig) -> OcrResult:
        bgr = _ensure_bgr(image)
        h, w = bgr.shape[:2]
        return OcrResult(
            blocks=(),
            full_text="",
            backend_name=self.name,
            image_width=int(w),
            image_height=int(h),
            applied=False,
        )


class JapaneseOcrEngine:
    """High-level Japanese/English OCR facade selecting the best available backend."""

    def __init__(
        self,
        config: OcrConfig | None = None,
        backends: Sequence[OcrBackend] | None = None,
    ) -> None:
        self.config = config if config is not None else OcrConfig()
        if backends is not None:
            self._backends: list[OcrBackend] = list(backends)
        elif self.config.prefer_native_backend:
            self._backends = [
                MacOSVisionOcrBackend(),
                RapidOcrBackend(),
                NoOpOcrBackend(),
            ]
        else:
            self._backends = [
                RapidOcrBackend(),
                MacOSVisionOcrBackend(),
                NoOpOcrBackend(),
            ]

    def available_backend_names(self) -> list[str]:
        """Return names of currently available backends in priority order."""
        return [b.name for b in self._backends if b.is_available()]

    def recognize(self, image: np.ndarray | bytes) -> OcrResult:
        """Run OCR on `image` (`ndarray` or encoded bytes), falling back safely on error."""
        if isinstance(image, (bytes, bytearray)):
            bgr = decode_image_bytes(image)
        else:
            bgr = _ensure_bgr(image)
        h, w = bgr.shape[:2]
        last_error = ""

        for backend in self._backends:
            if not backend.is_available():
                continue
            try:
                res = backend.recognize(bgr, self.config)
                if res.blocks and not any(b.stroke_width_norm > 0.0 for b in res.blocks):
                    return replace(
                        res,
                        blocks=_enrich_blocks_with_visual_metrics(res.blocks, bgr),
                    )
                return res
            except Exception as exc:
                last_error = str(exc)
                logger.warning("OCR backend '%s' failed: %s", backend.name, exc)

        return OcrResult(
            blocks=(),
            full_text="",
            backend_name="unavailable",
            image_width=int(w),
            image_height=int(h),
            applied=False,
            error_message=last_error,
        )
