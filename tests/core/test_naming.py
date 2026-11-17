"""Unit tests for `FileNamingEngine`."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from open_escl_client.core.naming import (
    FileNamingEngine,
    normalize_extension,
    sanitize_filename_stem,
)

FIXED_NOW = datetime(2026, 11, 18, 8, 5, 9)


def test_render_filename_with_date_time_and_sequence_tokens() -> None:
    engine = FileNamingEngine()

    assert (
        engine.render_filename("Scan_{date}_{seq:03d}", seq=1, file_format="PDF", now=FIXED_NOW)
        == "Scan_20261118_001.pdf"
    )
    assert (
        engine.render_filename(
            "領収書_{yyyy}-{mm}-{dd}_{time}_{seq:04d}",
            seq=42,
            file_format="JPEG",
            now=FIXED_NOW,
        )
        == "領収書_2026-11-18_080509_0042.jpg"
    )
    assert (
        engine.render_filename("Memo_{date_iso}_{seq}", seq=7, file_format="PNG", now=FIXED_NOW)
        == "Memo_2026-11-18_7.png"
    )


def test_render_filename_sanitizes_illegal_chars_and_handles_invalid_template() -> None:
    engine = FileNamingEngine()

    assert sanitize_filename_stem('A/B:C*D?"E<F>G|H ') == "A_B_C_D__E_F_G_H"
    assert normalize_extension("JPEG") == ".jpg"
    assert normalize_extension(".PNG") == ".png"

    # Incomplete brace while user is typing falls back safely without crashing
    fallback = engine.render_filename("Scan_{date_{seq", seq=2, file_format="PDF", now=FIXED_NOW)
    assert fallback == "Scan_20261118_002.pdf"

    # Template already ending with .pdf does not produce .pdf.pdf
    no_double_ext = engine.render_filename("Doc_{seq:02d}.pdf", seq=3, file_format="PDF", now=FIXED_NOW)
    assert no_double_ext == "Doc_03.pdf"


def test_resolve_next_path_avoids_overwriting_existing_files(tmp_path: Path) -> None:
    engine = FileNamingEngine()

    # Pre-create 001 and 002 in save directory
    (tmp_path / "Scan_20261118_001.jpg").write_bytes(b"a")
    (tmp_path / "Scan_20261118_002.jpg").write_bytes(b"b")

    next_path, used_seq = engine.resolve_next_path(
        tmp_path,
        "Scan_{date}_{seq:03d}",
        file_format="JPEG",
        start_seq=1,
        now=FIXED_NOW,
    )
    assert used_seq == 3
    assert next_path == tmp_path / "Scan_20261118_003.jpg"

    preview = engine.preview_next_filename(
        tmp_path,
        "Scan_{date}_{seq:03d}",
        file_format="JPEG",
        start_seq=1,
        now=FIXED_NOW,
    )
    assert preview == "Scan_20261118_003.jpg"


def test_resolve_next_path_without_seq_token_appends_suffix_on_collision(tmp_path: Path) -> None:
    engine = FileNamingEngine()

    # First save uses exact template name
    p1, seq1 = engine.resolve_next_path(
        tmp_path,
        "Contract_{date}",
        file_format="PDF",
        now=FIXED_NOW,
    )
    assert p1 == tmp_path / "Contract_20261118.pdf"
    assert seq1 == 1
    p1.write_bytes(b"%PDF")

    # Second save does NOT overwrite Contract_20261118.pdf
    p2, seq2 = engine.resolve_next_path(
        tmp_path,
        "Contract_{date}",
        file_format="PDF",
        now=FIXED_NOW,
    )
    assert p2 == tmp_path / "Contract_20261118_001.pdf"
    assert seq2 == 1
