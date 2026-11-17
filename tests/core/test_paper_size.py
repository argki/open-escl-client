"""Unit tests for `PaperSizeResolver`, orientation variants (Portrait/Landscape), and placement guides."""

from __future__ import annotations

import pytest

from open_escl_client.core.paper_size import PaperOrientation, PaperSizeResolver
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

    # A4 and B5 fit in Portrait, but NOT in Landscape (since 297mm / 257mm > 215.9mm width)
    assert "A4" in codes
    assert "A4_LANDSCAPE" not in codes
    assert "B5_JIS" in codes
    assert "B5_JIS_LANDSCAPE" not in codes

    # Smaller sizes (A5: 148x210mm, Postcard: 100x148mm, L photo, Business card) fit in BOTH orientations!
    assert "A5" in codes
    assert "A5_LANDSCAPE" in codes
    assert "POSTCARD" in codes
    assert "POSTCARD_LANDSCAPE" in codes
    assert "PHOTO_L" in codes
    assert "PHOTO_L_LANDSCAPE" in codes
    assert "BUSINESS_CARD" in codes
    assert "BUSINESS_CARD_LANDSCAPE" in codes
    assert "MAX" in codes

    # Larger sizes (A3, B4, Legal) must be excluded on an A4 flatbed
    assert "A3" not in codes
    assert "B4_JIS" not in codes
    assert "LEGAL" not in codes


def test_resolve_for_a3_scanner_mfc_j6580cdw_includes_a4_and_b5_landscape() -> None:
    # Brother MFC-J6580CDW (A3 capable): MaxWidth=3508 (~297mm), MaxHeight=4960 (~420mm)
    a3_platen = InputSourceCaps(
        source=InputSourceKind.PLATEN,
        min_width=100,
        max_width=3508,
        min_height=100,
        max_height=4960,
    )
    resolver = PaperSizeResolver()
    options = resolver.resolve_for_source(a3_platen)
    codes = [opt.code for opt in options]
    labels = [opt.display_label for opt in options]

    assert "A3" in codes
    assert "B4_JIS" in codes
    # On an A3 bed, A4 (210x297) and B5 (182x257) can be placed BOTH Portrait and Landscape!
    assert "A4" in codes
    assert "A4_LANDSCAPE" in codes
    assert "A4 縦置き (210 × 297 mm)" in labels
    assert "A4 横置き (297 × 210 mm)" in labels
    assert "B5_JIS" in codes
    assert "B5_JIS_LANDSCAPE" in codes
    assert "B5 (JIS) 縦置き (182 × 257 mm)" in labels
    assert "B5 (JIS) 横置き (257 × 182 mm)" in labels
    assert "LETTER" in codes
    assert "LETTER_LANDSCAPE" in codes


def test_placement_guide_ratios_for_a4_portrait_and_landscape_on_a3_bed() -> None:
    a3_platen = InputSourceCaps(
        source=InputSourceKind.PLATEN,
        max_width=3508,  # 297.0mm
        max_height=4960,  # 420.0mm
    )
    resolver = PaperSizeResolver()
    options = resolver.resolve_for_source(a3_platen)
    by_code = {opt.code: opt for opt in options}

    a4_portrait = by_code["A4"].build_placement_guide(a3_platen.max_width, a3_platen.max_height)
    assert a4_portrait.orientation == PaperOrientation.PORTRAIT
    assert pytest.approx(a4_portrait.width_ratio, abs=0.02) == 210.0 / 297.0
    assert pytest.approx(a4_portrait.height_ratio, abs=0.02) == 297.0 / 420.0
    assert "縦置き" in a4_portrait.placement_hint

    a4_landscape = by_code["A4_LANDSCAPE"].build_placement_guide(a3_platen.max_width, a3_platen.max_height)
    assert a4_landscape.orientation == PaperOrientation.LANDSCAPE
    assert pytest.approx(a4_landscape.width_ratio, abs=0.02) == 297.0 / 297.0
    assert pytest.approx(a4_landscape.height_ratio, abs=0.02) == 210.0 / 420.0
    assert "横置き" in a4_landscape.placement_hint


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
    assert "A5_LANDSCAPE" in codes
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
    assert a4_opt.display_label == "A4 縦置き (210 × 297 mm)"
