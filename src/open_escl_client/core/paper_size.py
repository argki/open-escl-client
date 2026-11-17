"""Standard paper size definitions, physical platen orientation matching, and placement guides.

Physical Platen Coordinate System (Front View of Multifunction Printers / Flatbed Scanners):
- Across Brother (e.g. MFC-J6580CDW), Canon, Epson, and HP flatbed scanners, the CIS sensor bar
  runs along the physical **depth** (front-to-back, shorter side = eSCL `MaxWidth`), while the
  carriage moves left-to-right along the physical **horizontal width** (longer side = eSCL `MaxHeight`).
- The physical reference mark (origin corner) is located at the **back-left (左奥)**.
- Therefore, when a user stands in front of the scanner:
  - Platen Horizontal Width (横方向) = `max(MaxWidth, MaxHeight)` (typically eSCL `MaxHeight`, e.g. 432 mm on A3 / 297 mm on A4)
  - Platen Depth (奥行き方向)        = `min(MaxWidth, MaxHeight)` (typically eSCL `MaxWidth`, e.g. 297 mm on A3 / 216 mm on A4)
  - **縦置き (Portrait from user's view)**: short edge along horizontal width, long edge along depth (front-to-back).
  - **横置き (Landscape from user's view)**: long edge along horizontal width, short edge along depth (front-to-back).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from open_escl_client.escl.capabilities import (
    InputSourceCaps,
    ScannerCapabilities,
    mm_to_three_hundredths_inch,
    three_hundredths_inch_to_mm,
)

# Allow up to ~1.2mm (15 units of 1/300 inch) rounding tolerance when matching standard sizes
DEFAULT_SIZE_TOLERANCE_UNITS = 15


class PaperOrientation(StrEnum):
    """Orientation of the paper placed on the scanner platen as seen from the front."""

    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"
    MAXIMUM = "maximum"


@dataclass(frozen=True, slots=True)
class PaperPlacementGuide:
    """Visual placement guide data comparing the paper against the physical scanner platen (front view)."""

    label: str
    paper_name: str
    orientation: PaperOrientation
    paper_horizontal_mm: float
    paper_depth_mm: float
    bed_horizontal_mm: float
    bed_depth_mm: float
    horizontal_ratio: float
    depth_ratio: float
    placement_hint: str

    def to_dict(self) -> dict[str, object]:
        """Convert to a dictionary suitable for QML `QVariantMap` consumption."""
        return {
            "label": self.label,
            "paperName": self.paper_name,
            "orientation": str(self.orientation),
            "paperWidthMm": round(self.paper_horizontal_mm, 1),
            "paperHeightMm": round(self.paper_depth_mm, 1),
            "bedWidthMm": round(self.bed_horizontal_mm, 1),
            "bedHeightMm": round(self.bed_depth_mm, 1),
            "widthRatio": max(0.05, min(1.0, self.horizontal_ratio)),
            "heightRatio": max(0.05, min(1.0, self.depth_ratio)),
            "placementHint": self.placement_hint,
        }


@dataclass(frozen=True, slots=True)
class PaperSizeOption:
    """A selectable paper size option with physical front-view dimensions and eSCL coordinate bounds.

    Attributes:
        code: Identifier code (e.g. `A4`, `A4_LANDSCAPE`, `MAX`).
        name: Base paper size name (e.g. `A4`, `B5 (JIS)`).
        width_mm: Physical horizontal width (横方向 mm) when placed on the platen from the front.
        height_mm: Physical depth (奥行き方向 mm) when placed on the platen from the front.
        width_units: eSCL `Width` (main-scan axis) in 1/300 inch units.
        height_units: eSCL `Height` (sub-scan axis) in 1/300 inch units.
        orientation: `PORTRAIT` (縦置き: depth > horizontal), `LANDSCAPE` (横置き: horizontal > depth), or `MAXIMUM`.
        is_maximum: True if this option scans the entire platen area.
    """

    code: str
    name: str
    width_mm: float
    height_mm: float
    width_units: int
    height_units: int
    orientation: PaperOrientation = PaperOrientation.PORTRAIT
    is_maximum: bool = False

    @property
    def orientation_label(self) -> str:
        """Japanese label for the placement orientation."""
        if self.is_maximum or self.orientation == PaperOrientation.MAXIMUM:
            return "全体"
        if self.orientation == PaperOrientation.LANDSCAPE:
            return "横置き"
        return "縦置き"

    @property
    def display_label(self) -> str:
        """Return a human-friendly label for UI dropdowns (`横 × 奥行き mm`)."""
        if self.is_maximum:
            return f"最大サイズ (横 {self.width_mm:.0f} × 奥行き {self.height_mm:.0f} mm / 自動トリミング用)"
        w_str = f"{self.width_mm:g}"
        h_str = f"{self.height_mm:g}"
        return f"{self.name} {self.orientation_label} (横 {w_str} × 奥行き {h_str} mm)"

    def build_placement_guide(
        self,
        escl_max_width_units: int,
        escl_max_height_units: int,
    ) -> PaperPlacementGuide:
        """Calculate visual placement ratios against the physical platen (horizontal = longer side, depth = shorter side)."""
        bed_horiz_units, bed_depth_units = _physical_bed_bounds(escl_max_width_units, escl_max_height_units)
        bed_horiz_mm = max(1.0, three_hundredths_inch_to_mm(bed_horiz_units))
        bed_depth_mm = max(1.0, three_hundredths_inch_to_mm(bed_depth_units))

        horiz_ratio = min(1.0, self.width_mm / bed_horiz_mm)
        depth_ratio = min(1.0, self.height_mm / bed_depth_mm)

        w_str = f"{self.width_mm:g}"
        h_str = f"{self.height_mm:g}"
        if self.is_maximum or self.orientation == PaperOrientation.MAXIMUM:
            hint = f"原稿台全体（横 {bed_horiz_mm:.0f} × 奥行き {bed_depth_mm:.0f} mm）を読み取ります。左奥の基準点を起点に配置してください。"
        elif self.orientation == PaperOrientation.LANDSCAPE:
            hint = f"【横置き】原稿台の「左奥」の基準点に合わせ、正面から見て横長（横 {w_str} mm × 奥行き {h_str} mm）に置いてください。"
        else:
            hint = f"【縦置き】原稿台の「左奥」の基準点に合わせ、正面から見て縦長（横 {w_str} mm × 奥行き {h_str} mm）に置いてください。"

        return PaperPlacementGuide(
            label=self.display_label,
            paper_name=self.name,
            orientation=self.orientation,
            paper_horizontal_mm=self.width_mm,
            paper_depth_mm=self.height_mm,
            bed_horizontal_mm=bed_horiz_mm,
            bed_depth_mm=bed_depth_mm,
            horizontal_ratio=horiz_ratio,
            depth_ratio=depth_ratio,
            placement_hint=hint,
        )


def _physical_bed_bounds(escl_max_width_units: int, escl_max_height_units: int) -> tuple[int, int]:
    """Return `(bed_horizontal_units, bed_depth_units)` as seen from the front of the scanner.

    On flatbed scanners, the physical horizontal width (left-to-right carriage travel) is the longer
    dimension (`max(MaxWidth, MaxHeight)`), and the physical depth (front-to-back sensor bar) is the
    shorter dimension (`min(MaxWidth, MaxHeight)`).
    """
    bed_horizontal_units = max(escl_max_width_units, escl_max_height_units)
    bed_depth_units = min(escl_max_width_units, escl_max_height_units)
    return bed_horizontal_units, bed_depth_units


def _map_physical_to_escl_units(
    horiz_units: int,
    depth_units: int,
    escl_max_width_units: int,
    escl_max_height_units: int,
) -> tuple[int, int]:
    """Map physical `(horizontal_units, depth_units)` to eSCL `(Width, Height)` clamped to scanner bounds."""
    if escl_max_height_units >= escl_max_width_units:
        # Standard eSCL flatbed: eSCL Width = physical depth, eSCL Height = physical horizontal width
        escl_w = min(depth_units, escl_max_width_units)
        escl_h = min(horiz_units, escl_max_height_units)
    else:
        escl_w = min(horiz_units, escl_max_width_units)
        escl_h = min(depth_units, escl_max_height_units)
    return escl_w, escl_h


@dataclass(frozen=True, slots=True)
class StandardPaperSpec:
    """Canonical specification of a standard paper size (`short_edge_mm <= long_edge_mm`)."""

    code: str
    name: str
    short_edge_mm: float
    long_edge_mm: float


STANDARD_PAPER_SPECS: tuple[StandardPaperSpec, ...] = (
    StandardPaperSpec("A3", "A3", 297.0, 420.0),
    StandardPaperSpec("B4_JIS", "B4 (JIS)", 257.0, 364.0),
    StandardPaperSpec("A4", "A4", 210.0, 297.0),
    StandardPaperSpec("B5_JIS", "B5 (JIS)", 182.0, 257.0),
    StandardPaperSpec("A5", "A5", 148.0, 210.0),
    StandardPaperSpec("B6_JIS", "B6 (JIS)", 128.0, 182.0),
    StandardPaperSpec("A6", "A6", 105.0, 148.0),
    StandardPaperSpec("LETTER", "レター (Letter)", 215.9, 279.4),
    StandardPaperSpec("LEGAL", "リーガル (Legal)", 215.9, 355.6),
    StandardPaperSpec("POSTCARD", "ハガキ", 100.0, 148.0),
    StandardPaperSpec("PHOTO_2L", "写真 2L判", 127.0, 178.0),
    StandardPaperSpec("PHOTO_L", "写真 L判", 89.0, 127.0),
    StandardPaperSpec("BUSINESS_CARD", "名刺", 55.0, 91.0),
)

# Backwards-compatible tuple of base PaperSizeOptions
STANDARD_PAPER_SIZES: tuple[PaperSizeOption, ...] = tuple(
    PaperSizeOption(
        code=spec.code,
        name=spec.name,
        width_mm=spec.short_edge_mm,
        height_mm=spec.long_edge_mm,
        width_units=mm_to_three_hundredths_inch(spec.short_edge_mm),
        height_units=mm_to_three_hundredths_inch(spec.long_edge_mm),
        orientation=PaperOrientation.PORTRAIT,
        is_maximum=False,
    )
    for spec in STANDARD_PAPER_SPECS
)


class PaperSizeResolver:
    """Resolve supported paper sizes and front-view orientations (Portrait / Landscape) for a scanner."""

    def __init__(
        self,
        standard_specs: tuple[StandardPaperSpec, ...] = STANDARD_PAPER_SPECS,
        tolerance_units: int = DEFAULT_SIZE_TOLERANCE_UNITS,
    ) -> None:
        self.standard_specs = standard_specs
        self.tolerance_units = tolerance_units

    def resolve_for_bounds(
        self,
        max_width_units: int,
        max_height_units: int,
        min_width_units: int = 0,
        min_height_units: int = 0,
    ) -> list[PaperSizeOption]:
        """Return all standard paper sizes and orientations that fit on the physical platen."""
        bed_horiz_units, bed_depth_units = _physical_bed_bounds(max_width_units, max_height_units)
        min_horiz_units = min(min_width_units, min_height_units)
        min_depth_units = min(min_width_units, min_height_units)

        supported: list[PaperSizeOption] = []

        for spec in self.standard_specs:
            short_units = mm_to_three_hundredths_inch(spec.short_edge_mm)
            long_units = mm_to_three_hundredths_inch(spec.long_edge_mm)

            # 1. Portrait from front view (縦置き: 横 = short_edge, 奥行き = long_edge)
            portrait_fits_max = (
                short_units <= bed_horiz_units + self.tolerance_units
                and long_units <= bed_depth_units + self.tolerance_units
            )
            portrait_fits_min = (
                short_units >= max(0, min_horiz_units - self.tolerance_units)
                and long_units >= max(0, min_depth_units - self.tolerance_units)
            )
            if portrait_fits_max and portrait_fits_min:
                escl_w, escl_h = _map_physical_to_escl_units(
                    short_units, long_units, max_width_units, max_height_units
                )
                supported.append(
                    PaperSizeOption(
                        code=spec.code,
                        name=spec.name,
                        width_mm=spec.short_edge_mm,
                        height_mm=spec.long_edge_mm,
                        width_units=escl_w,
                        height_units=escl_h,
                        orientation=PaperOrientation.PORTRAIT,
                        is_maximum=False,
                    )
                )

            # 2. Landscape from front view (横置き: 横 = long_edge, 奥行き = short_edge)
            if short_units != long_units:
                landscape_fits_max = (
                    long_units <= bed_horiz_units + self.tolerance_units
                    and short_units <= bed_depth_units + self.tolerance_units
                )
                landscape_fits_min = (
                    long_units >= max(0, min_horiz_units - self.tolerance_units)
                    and short_units >= max(0, min_depth_units - self.tolerance_units)
                )
                if landscape_fits_max and landscape_fits_min:
                    escl_w, escl_h = _map_physical_to_escl_units(
                        long_units, short_units, max_width_units, max_height_units
                    )
                    supported.append(
                        PaperSizeOption(
                            code=f"{spec.code}_LANDSCAPE",
                            name=spec.name,
                            width_mm=spec.long_edge_mm,
                            height_mm=spec.short_edge_mm,
                            width_units=escl_w,
                            height_units=escl_h,
                            orientation=PaperOrientation.LANDSCAPE,
                            is_maximum=False,
                        )
                    )

        max_option = PaperSizeOption(
            code="MAX",
            name="最大サイズ",
            width_mm=round(three_hundredths_inch_to_mm(bed_horiz_units), 1),
            height_mm=round(three_hundredths_inch_to_mm(bed_depth_units), 1),
            width_units=max_width_units,
            height_units=max_height_units,
            orientation=PaperOrientation.MAXIMUM,
            is_maximum=True,
        )
        supported.append(max_option)
        return supported

    def resolve_for_source(self, source_caps: InputSourceCaps) -> list[PaperSizeOption]:
        """Return supported paper sizes and orientations for a specific `InputSourceCaps`."""
        is_platen = str(source_caps.source) == "Platen"
        min_w = 0 if is_platen else source_caps.min_width
        min_h = 0 if is_platen else source_caps.min_height
        return self.resolve_for_bounds(
            max_width_units=source_caps.max_width,
            max_height_units=source_caps.max_height,
            min_width_units=min_w,
            min_height_units=min_h,
        )

    def resolve_for_capabilities(self, capabilities: ScannerCapabilities) -> list[PaperSizeOption]:
        """Return supported paper sizes and orientations for the scanner's primary input source."""
        return self.resolve_for_source(capabilities.primary_source)
