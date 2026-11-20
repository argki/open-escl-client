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
