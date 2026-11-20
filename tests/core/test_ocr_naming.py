"""Unit tests for `OcrNamingExtractor` (title, Japanese/Western dates, sanitization)."""

from __future__ import annotations

from open_escl_client.core.ocr_naming import OcrNamingExtractor
from open_escl_client.imaging.ocr import OcrBoundingBox, OcrResult, OcrTextBlock


def _make_ocr_result(entries: list[tuple[str, float, float, float]]) -> OcrResult:
    """Create an `OcrResult` from `(text, y, height, confidence)` tuples."""
    blocks = tuple(
        OcrTextBlock(
            text=text,
            bbox=OcrBoundingBox(x=0.1, y=y, width=0.7, height=h),
            confidence=conf,
        )
        for text, y, h, conf in entries
    )
    return OcrResult(
        blocks=blocks,
        full_text="\n".join(text for text, _, _, _ in entries),
        backend_name="mock_ocr",
        image_width=1000,
        image_height=1400,
        applied=True,
    )


def test_extracts_heading_keyword_and_wareki_reiwa_date() -> None:
    ocr = _make_ocr_result(
        [
            ("〒100-0001 東京都千代田区", 0.04, 0.02, 0.9),
            ("【御請求書】", 0.10, 0.055, 0.98),
            ("発行日：令和6年11月20日", 0.18, 0.025, 0.96),
            ("株式会社オープン・スキャン 御中", 0.24, 0.03, 0.95),
            ("合計金額: 54,000円", 0.55, 0.03, 0.97),
        ]
    )

    meta = OcrNamingExtractor().extract(ocr)
    assert meta.ocr_title == "御請求書"
    assert meta.ocr_date == "20241120"
    assert "株式会社オープン・スキャン" in meta.ocr_summary
    assert meta.to_tokens()["ocr_title"] == "御請求書"
    assert meta.to_tokens()["ocr_date"] == "20241120"


def test_extracts_ganen_wareki_abbr_and_western_dates() -> None:
    extractor = OcrNamingExtractor()

    reiwa_first = _make_ocr_result(
        [
            ("領収書", 0.08, 0.05, 0.95),
            ("令和元年5月1日", 0.18, 0.025, 0.92),
        ]
    )
    assert extractor.extract(reiwa_first).ocr_date == "20190501"

    heisei_abbr = _make_ocr_result(
        [
            ("医療費のお知らせ", 0.08, 0.04, 0.94),
            ("受付日: H31.4.30", 0.18, 0.025, 0.91),
        ]
    )
    meta_h = extractor.extract(heisei_abbr)
    assert meta_h.ocr_date == "20190430"
    assert "お知らせ" in meta_h.ocr_title

    western_slash = _make_ocr_result(
        [
            ("件名: 機器保守契約書 / 2026:A", 0.12, 0.04, 0.93),
            ("有効期限 2027/03/31", 0.30, 0.025, 0.90),
            ("作成日 2026/11/05", 0.22, 0.025, 0.95),
        ]
    )
    meta_w = extractor.extract(western_slash)
    # Prioritizes keyword-tagged "作成日" over earlier/other date
    assert meta_w.ocr_date == "20261105"
    # Illegal filename character ':' sanitized safely
    assert ":" not in meta_w.ocr_title
    assert "機器保守契約書" in meta_w.ocr_title


def test_handles_empty_or_unrecognized_ocr_safely() -> None:
    extractor = OcrNamingExtractor()

    assert extractor.extract(None).to_tokens() == {
        "ocr_title": "",
        "ocr_date": "",
        "ocr_summary": "",
    }

    empty_ocr = OcrResult(
        blocks=(),
        full_text="",
        backend_name="noop_fallback",
        image_width=100,
        image_height=100,
        applied=False,
    )
    assert extractor.extract(empty_ocr).ocr_title == ""

    numeric_only = _make_ocr_result([("12,345円", 0.2, 0.03, 0.95)])
    meta_num = extractor.extract(numeric_only)
    assert meta_num.ocr_title == ""
    assert meta_num.ocr_date == ""


def test_prefers_larger_and_bolder_font_over_smaller_keyword_lines() -> None:
    # Small upper keyword lines must not beat a much larger and bolder main title.
    ocr = OcrResult(
        blocks=(
            OcrTextBlock(
                text="請求書発行のお知らせ",
                bbox=OcrBoundingBox(x=0.08, y=0.06, width=0.35, height=0.018),
                confidence=0.98,
                stroke_width_norm=0.0018,
                ink_ratio=0.14,
            ),
            OcrTextBlock(
                text="件名: 振込明細",
                bbox=OcrBoundingBox(x=0.08, y=0.10, width=0.28, height=0.020),
                confidence=0.97,
                stroke_width_norm=0.0020,
                ink_ratio=0.16,
            ),
            OcrTextBlock(
                text="親展",
                bbox=OcrBoundingBox(x=0.78, y=0.08, width=0.12, height=0.065),
                confidence=0.95,
                stroke_width_norm=0.0080,
                ink_ratio=0.38,
            ),
            OcrTextBlock(
                text="株式会社サンプル 御中",
                bbox=OcrBoundingBox(x=0.08, y=0.15, width=0.45, height=0.030),
                confidence=0.96,
                stroke_width_norm=0.0028,
                ink_ratio=0.18,
            ),
            OcrTextBlock(
                text="年間電気料金ご契約プラン変更のご案内",
                bbox=OcrBoundingBox(x=0.10, y=0.22, width=0.78, height=0.052),
                confidence=0.93,
                stroke_width_norm=0.0072,
                ink_ratio=0.34,
            ),
            # Vertical narrow margin note with large bbox.height but small character width
            OcrTextBlock(
                text="このお知らせは大切に保管してください",
                bbox=OcrBoundingBox(x=0.92, y=0.20, width=0.022, height=0.38),
                confidence=0.92,
                stroke_width_norm=0.0016,
                ink_ratio=0.15,
            ),
        ),
        full_text="",
        backend_name="mock_ocr",
        image_width=1000,
        image_height=1400,
        applied=True,
    )

    meta = OcrNamingExtractor().extract(ocr)
    assert meta.ocr_title == "年間電気料金ご契約プラン変更のご案内"


def test_prefers_bolder_stroke_when_heights_are_comparable() -> None:
    ocr = OcrResult(
        blocks=(
            OcrTextBlock(
                text="利用控え",
                bbox=OcrBoundingBox(x=0.10, y=0.08, width=0.25, height=0.040),
                confidence=0.96,
                stroke_width_norm=0.0020,
                ink_ratio=0.12,
            ),
            OcrTextBlock(
                text="固定資産税納税通知",
                bbox=OcrBoundingBox(x=0.10, y=0.18, width=0.60, height=0.038),
                confidence=0.95,
                stroke_width_norm=0.0075,
                ink_ratio=0.38,
            ),
            OcrTextBlock(
                text="発行日 2026年11月20日",
                bbox=OcrBoundingBox(x=0.10, y=0.30, width=0.40, height=0.020),
                confidence=0.95,
                stroke_width_norm=0.0020,
                ink_ratio=0.14,
            ),
        ),
        full_text="",
        backend_name="mock_ocr",
        image_width=1000,
        image_height=1400,
        applied=True,
    )

    meta = OcrNamingExtractor().extract(ocr)
    assert meta.ocr_title == "固定資産税納税通知"
