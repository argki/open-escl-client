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
        title_block, ocr_title = self._extract_best_title(ocr_result.blocks)
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
    ) -> tuple[OcrTextBlock | None, str]:
        heights = sorted(max(1e-4, b.height_norm) for b in blocks)
        median_h = heights[len(heights) // 2]

        best_score = float("-inf")
        best_block: OcrTextBlock | None = None
        best_title = ""

        for idx, block in enumerate(blocks):
            raw = _nfkc(block.text)
            if not raw or _is_date_only_line(raw):
                continue
            cleaned = _clean_title_candidate(raw, self.max_title_length)
            if len(cleaned) < 2:
                continue

            score = 0.0
            # Prominent text height (typical document heading)
            rel_height = block.height_norm / max(1e-4, median_h)
            score += min(3.5, rel_height * 1.4)

            # Upper area of the page
            if block.bbox.y <= 0.30:
                score += 2.0 - block.bbox.y * 3.0
            elif block.bbox.y > 0.65:
                score -= 1.2

            # High-signal Japanese/English document type keyword match
            if any(kw in raw for kw in _DOCUMENT_TYPE_KEYWORDS):
                score += 4.5
            if _TITLE_PREFIX_RE.match(raw):
                score += 3.2
            if any(kw in raw for kw in ("TEL", "FAX", "〒", "http://", "https://")):
                score -= 3.5
            if len(raw) > 40:
                score -= 1.5
            if idx == 0:
                score += 0.4
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
