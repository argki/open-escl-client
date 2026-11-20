"""Extract structured document title and normalized date metadata from OCR results."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from open_escl_client.core.naming import sanitize_filename_stem
from open_escl_client.imaging.ocr import OcrResult, OcrTextBlock

_ERA_OFFSETS: dict[str, int] = {
    "令和": 2018,
    "平成": 1988,
    "昭和": 1925,
    "大正": 1911,
    "R": 2018,
    "H": 1988,
    "S": 1925,
    "T": 1911,
}

_DATE_PRIORITY_KEYWORDS: tuple[str, ...] = (
    "発行日",
    "請求日",
    "領収日",
    "作成日",
    "通知日",
    "受付日",
    "取引日",
    "ご利用日",
    "利用日",
    "日付",
    "年月日",
    "Date",
    "DATE",
)

# High-signal Japanese & English document type headings used in scanned paperwork.
_DOCUMENT_TYPE_KEYWORDS: tuple[str, ...] = (
    "御請求書",
    "請求書",
    "御見積書",
    "見積書",
    "領収証",
    "領収書",
    "レシート",
    "納品書",
    "注文書",
    "発注書",
    "契約書",
    "覚書",
    "通知書",
    "お知らせ",
    "ご案内",
    "証明書",
    "源泉徴収票",
    "利用明細書",
    "利用明細",
    "給与明細",
    "明細書",
    "確定申告",
    "申請書",
    "届出書",
    "報告書",
    "議事録",
    "履歴書",
    "診断書",
    "処方箋",
    "検診結果",
    "健康診断",
    "確定通知",
    "払込票",
    "Invoice",
    "Receipt",
    "Statement",
)

_TITLE_PREFIX_RE = re.compile(r"^(?:件名|表題|タイトル|文書名|Subject|Title)\s*[:：]\s*(.+)$", re.I)
_WAREKI_FULL_RE = re.compile(
    r"(令和|平成|昭和|大正)\s*(元|\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)
_WAREKI_ABBR_RE = re.compile(
    r"(?<![A-Za-z0-9])([RHST])\s*(元|\d{1,2})\s*[./\-年]\s*(\d{1,2})\s*[./\-月]\s*(\d{1,2})日?"
)
_SEIREKI_JP_RE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)
_SEIREKI_SEP_RE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})\s*[/\-.\s]\s*(\d{1,2})\s*[/\-.]\s*(\d{1,2})(?!\d)"
)
_PURE_NUMERIC_OR_SYMBOL_RE = re.compile(r"^[\d\s,./:\-+¥￥$円%()（）\[\]【】No.#-]+$")
_SUMMARY_ORG_HINT_RE = re.compile(
    r"(株式会社|有限会社|合同会社|商店|医院|病院|クリニック|薬局|市役所|区役所|銀行|支店|御中)"
)
_STAMP_BADGE_WORDS: frozenset[str] = frozenset(
    {
        "親展",
        "重要",
        "至急",
        "極秘",
        "秘",
        "社外秘",
        "取扱注意",
        "控",
        "控え",
        "写",
        "写し",
        "原本",
        "再発行",
        "丸秘",
        "COPY",
        "URGENT",
        "CONFIDENTIAL",
        "DRAFT",
        "SAMPLE",
    }
)
_CONTACT_OR_ADDR_RE = re.compile(r"(?:TEL|FAX|〒|https?://|^\d{3}-\d{4}|振込先|口座番号)", re.I)
_ADDRESSEE_SUFFIX_RE = re.compile(r"(?:御中|様|殿)$")


@dataclass(frozen=True, slots=True)
class OcrNamingMetadata:
    """Sanitized filename-ready metadata extracted from an `OcrResult`."""

    ocr_title: str = ""
    ocr_date: str = ""
    ocr_summary: str = ""
    raw_date_match: str = ""

    def to_tokens(self) -> dict[str, str]:
        """Return token mapping compatible with `FileNamingEngine`."""
        return {
            "ocr_title": self.ocr_title,
            "ocr_date": self.ocr_date,
            "ocr_summary": self.ocr_summary,
        }


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


def _validate_ymd(year: int, month: int, day: int) -> str | None:
    if not (1900 <= year <= 2100):
        return None
    try:
        dt = datetime(year, month, day)
    except ValueError:
        return None
    return dt.strftime("%Y%m%d")


def _extract_date_from_line(line: str) -> tuple[str, str] | None:
    normalized = _nfkc(line)
    if not normalized:
        return None

    for pattern, is_era in (
        (_WAREKI_FULL_RE, True),
        (_WAREKI_ABBR_RE, True),
        (_SEIREKI_JP_RE, False),
        (_SEIREKI_SEP_RE, False),
    ):
        for match in pattern.finditer(normalized):
            if is_era:
                era_key = match.group(1)
                raw_era_year = match.group(2)
                era_year = 1 if raw_era_year == "元" else int(raw_era_year)
                year = _ERA_OFFSETS[era_key] + era_year
                month = int(match.group(3))
                day = int(match.group(4))
            else:
                year = int(match.group(1))
                month = int(match.group(2))
                day = int(match.group(3))

            ymd = _validate_ymd(year, month, day)
            if ymd is not None:
                return ymd, match.group(0)
    return None


def _clean_title_candidate(raw: str, max_len: int) -> str:
    text = _nfkc(raw)
    prefix_match = _TITLE_PREFIX_RE.match(text)
    if prefix_match:
        text = prefix_match.group(1).strip()

    # Strip surrounding brackets/bullets and trim overly long slash translations
    text = text.strip(" 【】[]「」『』()（）■□◆◇●○★☆・:：-―—_/\\")
    if " / " in text and len(text) > 16:
        primary = text.split(" / ", 1)[0].strip()
        if len(primary) >= 2:
            text = primary

    text = re.sub(r"\s+", "_", text)
    if not text or _PURE_NUMERIC_OR_SYMBOL_RE.match(text):
        return ""
    sanitized = sanitize_filename_stem(text)
    if len(sanitized) > max_len:
        sanitized = sanitized[:max_len].rstrip(" _-")
    return sanitized


def _effective_char_height(
    block: OcrTextBlock,
    *,
    image_width: int = 0,
    image_height: int = 0,
) -> float:
    """Estimate single-line character height in normalized Y units (handles multi-line/vertical)."""
    raw = _nfkc(block.text)
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    n_lines = max(1, len(lines))
    h = max(1e-4, float(block.height_norm)) / float(n_lines)
    w = max(0.0, float(block.bbox.width))

    non_ws_len = len(re.sub(r"\s+", "", raw))
    if n_lines == 1 and non_ws_len >= 3 and w > 0.0:
        if image_width > 0 and image_height > 0:
            px_h = h * float(image_height)
            px_w = w * float(image_width)
            if px_h > px_w * 1.6:
                return max(1e-4, px_w / float(image_height))
        elif h > w * 2.0:
            return max(1e-4, w)
    return h


def _is_date_only_line(line: str) -> bool:
    normalized = _nfkc(line)
    found = _extract_date_from_line(normalized)
    if found is None:
        return False
    _, raw_match = found
    remainder = normalized.replace(raw_match, "")
    for kw in _DATE_PRIORITY_KEYWORDS:
        remainder = remainder.replace(kw, "")
    remainder = remainder.strip(" :：/-・()（）")
    return len(remainder) <= 2


class OcrNamingExtractor:
    """Extracts filename-ready `ocr_title`, `ocr_date`, and `ocr_summary` from `OcrResult`."""

    def __init__(self, *, max_title_length: int = 36, max_summary_length: int = 24) -> None:
        self.max_title_length = max(8, int(max_title_length))
        self.max_summary_length = max(8, int(max_summary_length))

    def extract(self, ocr_result: OcrResult | None) -> OcrNamingMetadata:
        """Extract sanitized naming metadata; returns empty fields when OCR has no text."""
        if ocr_result is None or not ocr_result.blocks:
            return OcrNamingMetadata()

        ocr_date, raw_date = self._extract_best_date(ocr_result.blocks)
        title_block, ocr_title = self._extract_best_title(
            ocr_result.blocks,
            image_width=ocr_result.image_width,
            image_height=ocr_result.image_height,
        )
        ocr_summary = self._extract_summary(ocr_result.blocks, excluded_block=title_block)

        return OcrNamingMetadata(
            ocr_title=ocr_title,
            ocr_date=ocr_date,
            ocr_summary=ocr_summary,
            raw_date_match=raw_date,
        )

    def _extract_best_date(self, blocks: tuple[OcrTextBlock, ...]) -> tuple[str, str]:
        best_priority: tuple[int, float] | None = None
        best_result: tuple[str, str] = ("", "")

        for block in blocks:
            text = _nfkc(block.text)
            extracted = _extract_date_from_line(text)
            if extracted is None:
                continue
            has_keyword = any(kw in text for kw in _DATE_PRIORITY_KEYWORDS)
            rank = (0 if has_keyword else 1, block.bbox.y)
            if best_priority is None or rank < best_priority:
                best_priority = rank
                best_result = extracted
        return best_result

    def _extract_best_title(
        self,
        blocks: tuple[OcrTextBlock, ...],
        *,
        image_width: int = 0,
        image_height: int = 0,
    ) -> tuple[OcrTextBlock | None, str]:
        char_heights = [
            _effective_char_height(b, image_width=image_width, image_height=image_height)
            for b in blocks
        ]
        sorted_heights = sorted(char_heights)
        median_h = sorted_heights[len(sorted_heights) // 2]

        valid_strokes = sorted(
            b.stroke_width_norm
            for b in blocks
            if b.stroke_width_norm > 0.0 and b.ink_ratio <= 0.72
        )
        median_stroke = valid_strokes[len(valid_strokes) // 2] if valid_strokes else 0.0

        valid_weights = sorted(
            b.stroke_width_norm / max(1e-4, ch)
            for b, ch in zip(blocks, char_heights, strict=True)
            if b.stroke_width_norm > 0.0 and b.ink_ratio <= 0.72
        )
        median_weight = valid_weights[len(valid_weights) // 2] if valid_weights else 0.0

        candidates: list[tuple[OcrTextBlock, str, str, float, bool]] = []
        for block, ch in zip(blocks, char_heights, strict=True):
            raw = _nfkc(block.text)
            if not raw or _is_date_only_line(raw):
                continue
            cleaned = _clean_title_candidate(raw, self.max_title_length)
            if len(cleaned) < 2:
                continue
            is_auxiliary = (
                cleaned.upper() in _STAMP_BADGE_WORDS
                or cleaned in _STAMP_BADGE_WORDS
                or bool(_CONTACT_OR_ADDR_RE.search(raw))
                or bool(_ADDRESSEE_SUFFIX_RE.search(raw))
            )
            candidates.append((block, raw, cleaned, ch, is_auxiliary))

        if not candidates:
            return None, ""

        primary_candidates = [c for c in candidates if not c[4]] or candidates
        max_h = max(c[3] for c in primary_candidates)
        max_stroke = max(
            (
                c[0].stroke_width_norm
                for c in primary_candidates
                if c[0].stroke_width_norm > 0.0 and c[0].ink_ratio <= 0.72
            ),
            default=0.0,
        )

        best_score = float("-inf")
        best_block: OcrTextBlock | None = None
        best_title = ""

        for block, raw, cleaned, char_h, _is_aux in candidates:
            score = 0.0

            # Font height is the primary signal (uncapped relative prominence + ratio to page max).
            rel_height = char_h / max(1e-4, median_h)
            ratio_to_max_h = min(1.2, char_h / max(1e-4, max_h))
            score += min(14.0, (rel_height**1.15) * 3.0)
            score += (ratio_to_max_h**1.5) * 5.0

            # Font stroke thickness & relative boldness weight (when measured from source image).
            if (
                median_stroke > 0.0
                and max_stroke > 0.0
                and block.stroke_width_norm > 0.0
                and block.ink_ratio <= 0.72
            ):
                rel_stroke = block.stroke_width_norm / max(1e-5, median_stroke)
                ratio_to_max_s = min(1.2, block.stroke_width_norm / max(1e-5, max_stroke))
                score += min(8.0, (rel_stroke**1.1) * 2.0)
                score += (ratio_to_max_s**1.3) * 3.5

                if median_weight > 0.0:
                    weight = block.stroke_width_norm / max(1e-4, char_h)
                    rel_weight = weight / median_weight
                    score += max(-0.8, min(3.0, (rel_weight - 1.0) * 2.5))

            # Gentle layout bias: prefer upper/upper-middle headings, penalize bottom footers.
            if block.bbox.y <= 0.45:
                score += 1.2 - block.bbox.y * 1.5
            elif block.bbox.y > 0.70:
                score -= 1.8

            # Secondary tie-breakers (must not override larger bold titles).
            has_doc_kw = any(kw in raw for kw in _DOCUMENT_TYPE_KEYWORDS)
            if has_doc_kw:
                score += 2.0
            if _TITLE_PREFIX_RE.match(raw):
                score += 2.2

            if cleaned in _STAMP_BADGE_WORDS or cleaned.upper() in _STAMP_BADGE_WORDS:
                score -= 6.0
            if _CONTACT_OR_ADDR_RE.search(raw):
                score -= 4.5
            if _ADDRESSEE_SUFFIX_RE.search(raw):
                score -= 3.0
            if len(cleaned) == 2 and not has_doc_kw:
                score -= 0.8
            if len(raw) > 45 or "。" in raw:
                score -= 2.5

            score += float(block.confidence) * 0.5

            if score > best_score:
                best_score = score
                best_block = block
                best_title = cleaned

        return best_block, best_title

    def _extract_summary(
        self,
        blocks: tuple[OcrTextBlock, ...],
        *,
        excluded_block: OcrTextBlock | None,
    ) -> str:
        best_score = float("-inf")
        best_summary = ""
        for block in blocks:
            if block is excluded_block or block.bbox.y > 0.6:
                continue
            raw = _nfkc(block.text)
            if not raw or _is_date_only_line(raw):
                continue
            if any(kw in raw for kw in ("TEL", "FAX", "〒")):
                continue
            cleaned = _clean_title_candidate(raw, self.max_summary_length)
            if len(cleaned) < 2:
                continue

            score = 1.0 - block.bbox.y
            if _SUMMARY_ORG_HINT_RE.search(raw):
                score += 3.0
            if _TITLE_PREFIX_RE.match(raw):
                score += 2.0
            if score > best_score:
                best_score = score
                best_summary = cleaned
        return best_summary
