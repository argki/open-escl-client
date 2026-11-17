"""Standard paper size definitions and scanner capability matching (`PaperSizeResolver`)."""

from __future__ import annotations

from dataclasses import dataclass

from open_escl_client.escl.capabilities import (
    InputSourceCaps,
    ScannerCapabilities,
    mm_to_three_hundredths_inch,
    three_hundredths_inch_to_mm,
)

# Allow up to ~1.2mm (15 units of 1/300 inch) rounding tolerance when matching standard sizes
# (e.g., A4 height 297.0mm is 3507.87 units; some scanners report 3500 or 3507 as MaxHeight).
DEFAULT_SIZE_TOLERANCE_UNITS = 15


@dataclass(frozen=True, slots=True)
class PaperSizeOption:
    """A selectable paper size option with physical dimensions and eSCL coordinate bounds."""

    code: str
    name: str
    width_mm: float
    height_mm: float
    width_units: int
    height_units: int
    is_maximum: bool = False

    @property
    def display_label(self) -> str:
        """Return a human-friendly label for UI dropdowns."""
        if self.is_maximum:
            return f"最大サイズ ({self.width_mm:.0f} × {self.height_mm:.0f} mm / 自動トリミング用)"
        w_str = f"{self.width_mm:g}"
        h_str = f"{self.height_mm:g}"
        return f"{self.name} ({w_str} × {h_str} mm)"

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
            is_maximum=self.is_maximum,
        )


def _std(code: str, name: str, width_mm: float, height_mm: float) -> PaperSizeOption:
    return PaperSizeOption(
        code=code,
        name=name,
        width_mm=width_mm,
        height_mm=height_mm,
        width_units=mm_to_three_hundredths_inch(width_mm),
        height_units=mm_to_three_hundredths_inch(height_mm),
        is_maximum=False,
    )


# Standard JIS / ISO / North American / Photo paper sizes ordered for Japanese users
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
    """Resolve supported paper sizes for a given scanner or input source."""

    def __init__(
        self,
        standard_sizes: tuple[PaperSizeOption, ...] = STANDARD_PAPER_SIZES,
        tolerance_units: int = DEFAULT_SIZE_TOLERANCE_UNITS,
    ) -> None:
        self.standard_sizes = standard_sizes
        self.tolerance_units = tolerance_units

    def resolve_for_bounds(
        self,
        max_width_units: int,
        max_height_units: int,
        min_width_units: int = 0,
        min_height_units: int = 0,
    ) -> list[PaperSizeOption]:
        """Return all standard paper sizes that fit within the given scanner bounds, plus Maximum."""
        supported: list[PaperSizeOption] = []

        for candidate in self.standard_sizes:
            fits_max = (
                candidate.width_units <= max_width_units + self.tolerance_units
                and candidate.height_units <= max_height_units + self.tolerance_units
            )
            fits_min = (
                candidate.width_units >= max(0, min_width_units - self.tolerance_units)
                and candidate.height_units >= max(0, min_height_units - self.tolerance_units)
            )
            if fits_max and fits_min:
                supported.append(candidate.clamped_to(max_width_units, max_height_units))

        max_option = PaperSizeOption(
            code="MAX",
            name="最大サイズ",
            width_mm=round(three_hundredths_inch_to_mm(max_width_units), 1),
            height_mm=round(three_hundredths_inch_to_mm(max_height_units), 1),
            width_units=max_width_units,
            height_units=max_height_units,
            is_maximum=True,
        )
        supported.append(max_option)
        return supported

    def resolve_for_source(self, source_caps: InputSourceCaps) -> list[PaperSizeOption]:
        """Return supported paper sizes for a specific `InputSourceCaps` (e.g., Platen or ADF)."""
        # For Platen (flatbed), ignore min_width/min_height restrictions for small items (cards/photos)
        # because small items can always be scanned on the glass bed.
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
        """Return supported paper sizes for the scanner's primary input source."""
        return self.resolve_for_source(capabilities.primary_source)
