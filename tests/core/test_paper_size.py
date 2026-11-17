"""Unit tests for `PaperSizeResolver` and standard paper size matching."""

from __future__ import annotations

from open_escl_client.core.paper_size import PaperSizeResolver
from open_escl_client.escl.capabilities import InputSourceCaps, InputSourceKind


def test_resolve_for_standard_a4_flatbed_scanner() -> None:
    # Brother A4 flatbed: MaxWidth=2550 (215.9mm), MaxHeight=3508 (297.0mm)
    platen = InputSourceCaps(
        source=InputSourceKind.PLATEN,
        min_width=96,
        max_width=2550,
        min_height=96,
        max_height=3508,
    )
    resolver = PaperSizeResolver()
    options = resolver.resolve_for_source(platen)
    codes = [opt.code for opt in options]

    # A4, B5, A5, Letter, Postcard, L/2L photo, Business card, and MAX must be available
    assert "A4" in codes
    assert "B5_JIS" in codes
    assert "A5" in codes
    assert "LETTER" in codes
    assert "POSTCARD" in codes
    assert "PHOTO_L" in codes
    assert "BUSINESS_CARD" in codes
    assert "MAX" in codes

    # Larger sizes (A3, B4, Legal) must be excluded on an A4 flatbed
    assert "A3" not in codes
    assert "B4_JIS" not in codes
    assert "LEGAL" not in codes


def test_resolve_for_a3_scanner_includes_a3_and_b4() -> None:
    a3_platen = InputSourceCaps(
        source=InputSourceKind.PLATEN,
        min_width=100,
        max_width=3508,  # ~297mm
        min_height=100,
        max_height=4960,  # ~420mm
    )
    resolver = PaperSizeResolver()
    options = resolver.resolve_for_source(a3_platen)
    codes = [opt.code for opt in options]

    assert "A3" in codes
    assert "B4_JIS" in codes
    assert "A4" in codes
    assert "B5_JIS" in codes
    assert "LEGAL" in codes
    assert "MAX" in codes


def test_resolve_for_brother_adf_includes_legal_and_respects_min_size() -> None:
    # Brother ADF: MinWidth=1748 (~148mm, A5 width), MaxWidth=2550, MaxHeight=4200 (~355.6mm, Legal)
    adf = InputSourceCaps(
        source=InputSourceKind.ADF_SIMPLEX,
        min_width=1748,
        max_width=2550,
        min_height=1748,
        max_height=4200,
    )
    resolver = PaperSizeResolver()
    options = resolver.resolve_for_source(adf)
    codes = [opt.code for opt in options]

    assert "A4" in codes
    assert "B5_JIS" in codes
    assert "A5" in codes
    assert "LETTER" in codes
    assert "LEGAL" in codes
    # Business card (55x91mm) is smaller than ADF minimum feed size (148x148mm)
    assert "BUSINESS_CARD" not in codes


def test_tolerance_and_clamping_for_slightly_short_a4_height() -> None:
    # Some scanners report 3500 units (296.3mm) instead of 3508 (297.0mm)
    platen = InputSourceCaps(
        source=InputSourceKind.PLATEN,
        max_width=2550,
        max_height=3500,
    )
    resolver = PaperSizeResolver()
    options = resolver.resolve_for_source(platen)
    a4_opt = next(opt for opt in options if opt.code == "A4")

    # Clamped safely to 3500 so the scanner does not reject ScanRegions
    assert a4_opt.height_units == 3500
    assert a4_opt.display_label == "A4 (210 × 297 mm)"
