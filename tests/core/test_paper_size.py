"""Unit tests for `PaperSizeResolver` using physical front-view platen coordinates."""

from __future__ import annotations

import pytest

from open_escl_client.core.paper_size import PaperOrientation, PaperSizeResolver
from open_escl_client.escl.capabilities import InputSourceCaps, InputSourceKind


def test_resolve_for_standard_a4_flatbed_scanner() -> None:
    # Standard A4 flatbed: eSCL MaxWidth=2550 (depth 215.9mm), MaxHeight=3508 (horizontal 297.0mm)
    # Physical platen from front view: Horizontal = 297.0mm, Depth = 215.9mm
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

    # On an A4 platen (Horizontal 297mm × Depth 216mm), A4 (210×297) fits in Landscape (横297×奥行210),
    # while A5 (148×210), Postcard, L photo, and Business card fit in BOTH Portrait and Landscape!
    assert "A4_LANDSCAPE" in codes
    assert "A4" not in codes
    assert "B5_JIS_LANDSCAPE" in codes
    assert "B5_JIS" not in codes

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
    assert "A3_LANDSCAPE" not in codes
    assert "B4_JIS_LANDSCAPE" not in codes


def test_resolve_for_a3_scanner_mfc_j6580cdw_includes_a4_and_b5_both_orientations() -> None:
    # Brother MFC-J6580CDW (A3 capable):
    # eSCL MaxWidth=3508 (depth ~297mm), MaxHeight=4960..5100 (horizontal ~420..432mm)
    # Physical platen from front view: Horizontal = 420mm, Depth = 297mm
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

    # A3 (420x297) and B4 (364x257) fit in Landscape (横長) on the horizontal A3 bed
    assert "A3_LANDSCAPE" in codes
    assert "B4_JIS_LANDSCAPE" in codes

    # A4 (210x297) and B5 (182x257) fit in BOTH Portrait (縦置き) and Landscape (横置き)!
    assert "A4" in codes
    assert "A4_LANDSCAPE" in codes
    assert "A4 縦置き (横 210 × 奥行き 297 mm)" in labels
    assert "A4 横置き (横 297 × 奥行き 210 mm)" in labels
    assert "B5_JIS" in codes
    assert "B5_JIS_LANDSCAPE" in codes
    assert "B5 (JIS) 縦置き (横 182 × 奥行き 257 mm)" in labels
    assert "B5 (JIS) 横置き (横 257 × 奥行き 182 mm)" in labels
    assert "LETTER" in codes
    assert "LETTER_LANDSCAPE" in codes


def test_placement_guide_ratios_and_escl_mapping_on_mfc_j6580cdw_bed() -> None:
    # Brother MFC-J6580CDW: eSCL MaxWidth=3508 (depth 297mm), MaxHeight=5100 (horizontal 431.8mm)
    a3_platen = InputSourceCaps(
        source=InputSourceKind.PLATEN,
        max_width=3508,
        max_height=5100,
    )
    resolver = PaperSizeResolver()
    options = resolver.resolve_for_source(a3_platen)
    by_code = {opt.code: opt for opt in options}

    # 1. A4 縦置き (正面から見て 横 210mm × 奥行き 297mm)
    a4_portrait_opt = by_code["A4"]
    # eSCL Width (depth axis) = 297mm (3508 units), eSCL Height (horizontal axis) = 210mm (2480 units)
    assert a4_portrait_opt.width_units == 3508
    assert a4_portrait_opt.height_units == 2480

    guide_p = a4_portrait_opt.build_placement_guide(a3_platen.max_width, a3_platen.max_height)
    assert guide_p.orientation == PaperOrientation.PORTRAIT
    assert guide_p.bed_horizontal_mm > guide_p.bed_depth_mm  # Bed is wider than deep!
    assert pytest.approx(guide_p.horizontal_ratio, abs=0.02) == 210.0 / 431.8
    assert pytest.approx(guide_p.depth_ratio, abs=0.02) == 297.0 / 297.0
    assert "左奥" in guide_p.placement_hint
    assert "縦置き" in guide_p.placement_hint

    # 2. A4 横置き (正面から見て 横 297mm × 奥行き 210mm)
    a4_landscape_opt = by_code["A4_LANDSCAPE"]
    # eSCL Width (depth axis) = 210mm (2480 units), eSCL Height (horizontal axis) = 297mm (3508 units)
    assert a4_landscape_opt.width_units == 2480
    assert a4_landscape_opt.height_units == 3508

    guide_l = a4_landscape_opt.build_placement_guide(a3_platen.max_width, a3_platen.max_height)
    assert guide_l.orientation == PaperOrientation.LANDSCAPE
    assert pytest.approx(guide_l.horizontal_ratio, abs=0.02) == 297.0 / 431.8
    assert pytest.approx(guide_l.depth_ratio, abs=0.02) == 210.0 / 297.0
    assert "左奥" in guide_l.placement_hint
    assert "横置き" in guide_l.placement_hint


def test_resolve_for_brother_adf_respects_min_size() -> None:
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

    assert "A4_LANDSCAPE" in codes
    assert "B5_JIS_LANDSCAPE" in codes
    assert "A5" in codes
    assert "A5_LANDSCAPE" in codes
    assert "LEGAL_LANDSCAPE" in codes
    # Business card (55x91mm) is smaller than ADF minimum feed size (148x148mm)
    assert "BUSINESS_CARD" not in codes
    assert "BUSINESS_CARD_LANDSCAPE" not in codes
