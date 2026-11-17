"""Standard paper size definitions, orientation variants, and scanner capability matching."""

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
    """Orientation of the paper placed on the scanner platen or feeder."""

    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"
    MAXIMUM = "maximum"


@dataclass(frozen=True, slots=True)
class PaperPlacementGuide:
    """Visual placement guide data comparing the paper dimensions against the scanner platen."""

    label: str
    paper_name: str
    orientation: PaperOrientation
    paper_width_mm: float
    paper_height_mm: float
    bed_width_mm: float
    bed_height_mm: float
    width_ratio: float
    height_ratio: float
    placement_hint: str

    def to_dict(self) -> dict[str, object]:
        """Convert to a dictionary suitable for QML `QVariantMap` consumption."""
        return {
            "label": self.label,
            "paperName": self.paper_name,
            "orientation": str(self.orientation),
            "paperWidthMm": round(self.paper_width_mm, 1),
            "paperHeightMm": round(self.paper_height_mm, 1),
            "bedWidthMm": round(self.bed_width_mm, 1),
            "bedHeightMm": round(self.bed_height_mm, 1),
            "widthRatio": max(0.05, min(1.0, self.width_ratio)),
            "heightRatio": max(0.05, min(1.0, self.height_ratio)),
            "placementHint": self.placement_hint,
        }


@dataclass(frozen=True, slots=True)
class PaperSizeOption:
    """A selectable paper size option with physical dimensions, orientation, and eSCL bounds."""

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
        """Return a human-friendly label for UI dropdowns."""
        if self.is_maximum:
            return f"最大サイズ ({self.width_mm:.0f} × {self.height_mm:.0f} mm / 自動トリミング用)"
        w_str = f"{self.width_mm:g}"
        h_str = f"{self.height_mm:g}"
        return f"{self.name} {self.orientation_label} ({w_str} × {h_str} mm)"

    def to_landscape(self) -> PaperSizeOption:
        """Return the rotated (landscape: long edge on top, short edge vertical) variant."""
        return PaperSizeOption(
            code=f"{self.code}_LANDSCAPE",
            name=self.name,
            width_mm=self.height_mm,
            height_mm=self.width_mm,
            width_units=self.height_units,
            height_units=self.width_units,
            orientation=PaperOrientation.LANDSCAPE,
            is_maximum=False,
        )

    def clamped_to(self, max_width_units: int, max_height_units: int) -> PaperSizeOption:
        """Return a copy whose eSCL units are safely clamped within the scanner's maximum bounds."""
        clamped_w = min(self.width_units, max_width_units)
        clamped_h = min(self.height_units, max_height_units)
        return PaperSizeOption(
            code=self.code,
            name=self.name,
            width_mm=self.width_mm,
            height_mm=self.height_mm,
            width_units=clamped_w,
            height_units=clamped_h,
            orientation=self.orientation,
            is_maximum=self.is_maximum,
        )

    def build_placement_guide(self, bed_max_width_units: int, bed_max_height_units: int) -> PaperPlacementGuide:
        """Calculate visual placement ratios and user guidance text against the scanner bed size."""
        bed_w_mm = max(1.0, three_hundredths_inch_to_mm(bed_max_width_units))
        bed_h_mm = max(1.0, three_hundredths_inch_to_mm(bed_max_height_units))
        w_ratio = min(1.0, self.width_mm / bed_w_mm)
        h_ratio = min(1.0, self.height_mm / bed_h_mm)

        w_str = f"{self.width_mm:g}"
        h_str = f"{self.height_mm:g}"
        if self.is_maximum or self.orientation == PaperOrientation.MAXIMUM:
            hint = f"原稿台全体（{bed_w_mm:.0f} × {bed_h_mm:.0f} mm）を読み取ります。原稿の向きを問わず配置できます。"
        elif self.orientation == PaperOrientation.LANDSCAPE:
            hint = f"【横置き】原稿台の基準角に合わせ、長辺（{w_str} mm）を上辺・短辺（{h_str} mm）を縦にして横向きに置いてください。"
        else:
            hint = f"【縦置き】原稿台の基準角に合わせ、短辺（{w_str} mm）を上辺・長辺（{h_str} mm）を縦にして縦向きに置いてください。"

        return PaperPlacementGuide(
            label=self.display_label,
            paper_name=self.name,
            orientation=self.orientation,
            paper_width_mm=self.width_mm,
            paper_height_mm=self.height_mm,
            bed_width_mm=bed_w_mm,
            bed_height_mm=bed_h_mm,
            width_ratio=w_ratio,
            height_ratio=h_ratio,
            placement_hint=hint,
        )


def _std(code: str, name: str, width_mm: float, height_mm: float) -> PaperSizeOption:
    return PaperSizeOption(
        code=code,
        name=name,
        width_mm=width_mm,
        height_mm=height_mm,
        width_units=mm_to_three_hundredths_inch(width_mm),
        height_units=mm_to_three_hundredths_inch(height_mm),
        orientation=PaperOrientation.PORTRAIT,
        is_maximum=False,
    )


# Standard JIS / ISO / North American / Photo paper sizes (base portrait definitions)
STANDARD_PAPER_SIZES: tuple[PaperSizeOption, ...] = (
    _std("A3", "A3", 297.0, 420.0),
    _std("B4_JIS", "B4 (JIS)", 257.0, 364.0),
    _std("A4", "A4", 210.0, 297.0),
    _std("B5_JIS", "B5 (JIS)", 182.0, 257.0),
    _std("A5", "A5", 148.0, 210.0),
    _std("B6_JIS", "B6 (JIS)", 128.0, 182.0),
    _std("A6", "A6", 105.0, 148.0),
    _std("LETTER", "レター (Letter)", 215.9, 279.4),
    _std("LEGAL", "リーガル (Legal)", 215.9, 355.6),
    _std("POSTCARD", "ハガキ", 100.0, 148.0),
    _std("PHOTO_2L", "写真 2L判", 127.0, 178.0),
    _std("PHOTO_L", "写真 L判", 89.0, 127.0),
    _std("BUSINESS_CARD", "名刺", 55.0, 91.0),
)


class PaperSizeResolver:
    """Resolve supported paper sizes (including both Portrait and Landscape orientations) for a scanner."""

    def __init__(
        self,
        standard_sizes: tuple[PaperSizeOption, ...] = STANDARD_PAPER_SIZES,
        tolerance_units: int = DEFAULT_SIZE_TOLERANCE_UNITS,
    ) -> None:
        self.standard_sizes = standard_sizes
        self.tolerance_units = tolerance_units

    def _fits_bounds(
        self,
        option: PaperSizeOption,
        max_width_units: int,
        max_height_units: int,
        min_width_units: int,
        min_height_units: int,
    ) -> bool:
        fits_max = (
            option.width_units <= max_width_units + self.tolerance_units
            and option.height_units <= max_height_units + self.tolerance_units
        )
        fits_min = (
            option.width_units >= max(0, min_width_units - self.tolerance_units)
            and option.height_units >= max(0, min_height_units - self.tolerance_units)
        )
        return fits_max and fits_min

    def resolve_for_bounds(
        self,
        max_width_units: int,
        max_height_units: int,
        min_width_units: int = 0,
        min_height_units: int = 0,
    ) -> list[PaperSizeOption]:
        """Return all standard paper sizes and orientations (Portrait / Landscape) that fit within the bounds."""
        supported: list[PaperSizeOption] = []

        for base_portrait in self.standard_sizes:
            # 1. Check Portrait (縦置き)
            if self._fits_bounds(base_portrait, max_width_units, max_height_units, min_width_units, min_height_units):
                supported.append(base_portrait.clamped_to(max_width_units, max_height_units))

            # 2. Check Landscape (横置き) if non-square
            if base_portrait.width_units != base_portrait.height_units:
                landscape = base_portrait.to_landscape()
                if self._fits_bounds(landscape, max_width_units, max_height_units, min_width_units, min_height_units):
                    supported.append(landscape.clamped_to(max_width_units, max_height_units))

        max_option = PaperSizeOption(
            code="MAX",
            name="最大サイズ",
            width_mm=round(three_hundredths_inch_to_mm(max_width_units), 1),
            height_mm=round(three_hundredths_inch_to_mm(max_height_units), 1),
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
