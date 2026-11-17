"""eSCL `/eSCL/ScannerCapabilities` XML parser and vendor adapter (optimized for Brother)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import httpx
from lxml import etree

THREE_HUNDREDTHS_PER_INCH = 300.0
MM_PER_INCH = 25.4

STANDARD_CANDIDATE_DPIS: tuple[int, ...] = (75, 100, 150, 200, 300, 400, 600, 1200)


def three_hundredths_inch_to_mm(units: int | float) -> float:
    """Convert eSCL ThreeHundredthsOfInches (1/300 inch) to millimeters."""
    return (float(units) * MM_PER_INCH) / THREE_HUNDREDTHS_PER_INCH


def mm_to_three_hundredths_inch(mm: int | float) -> int:
    """Convert millimeters to eSCL ThreeHundredthsOfInches (1/300 inch)."""
    return int(round((float(mm) * THREE_HUNDREDTHS_PER_INCH) / MM_PER_INCH))


class InputSourceKind(StrEnum):
    """Supported scanner input sources."""

    PLATEN = "Platen"
    ADF_SIMPLEX = "AdfSimplex"
    ADF_DUPLEX = "AdfDuplex"


def _local_name(element: etree._Element) -> str:
    """Return the XML tag local name stripped of any namespace URI."""
    tag = element.tag
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _find_child(parent: etree._Element, local_tag: str) -> etree._Element | None:
    """Find the first direct child matching `local_tag` regardless of XML namespace."""
    for child in parent:
        if _local_name(child) == local_tag:
            return child
    return None


def _find_descendants(parent: etree._Element, local_tag: str) -> list[etree._Element]:
    """Find all descendant elements matching `local_tag` regardless of XML namespace."""
    return [elem for elem in parent.iter() if _local_name(elem) == local_tag]


def _child_text(parent: etree._Element, local_tag: str, default: str = "") -> str:
    child = _find_child(parent, local_tag)
    if child is not None and child.text is not None:
        return child.text.strip()
    return default


def _child_int(parent: etree._Element, local_tag: str, default: int = 0) -> int:
    text = _child_text(parent, local_tag, "")
    if not text:
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class InputSourceCaps:
    """Capabilities of a specific input source (Platen, ADF Simplex, or ADF Duplex)."""

    source: InputSourceKind
    min_width: int = 0
    max_width: int = 2550  # Default Letter/A4 width (~8.5 inch) in 1/300"
    min_height: int = 0
    max_height: int = 3508  # Default A4 height (~297 mm) in 1/300"
    resolutions: tuple[int, ...] = (150, 300, 600)
    color_modes: tuple[str, ...] = ("RGB24", "Grayscale8")
    document_formats: tuple[str, ...] = ("image/jpeg", "application/pdf")
    max_optical_x_resolution: int | None = None
    max_optical_y_resolution: int | None = None

    @property
    def max_width_mm(self) -> float:
        """Maximum scan width in millimeters."""
        return three_hundredths_inch_to_mm(self.max_width)

    @property
    def max_height_mm(self) -> float:
        """Maximum scan height in millimeters."""
        return three_hundredths_inch_to_mm(self.max_height)

    @property
    def preview_dpi(self) -> int:
        """Return the lowest suitable resolution (75-150 dpi preferred) for fast preview."""
        if not self.resolutions:
            return 100
        sorted_dpis = sorted(self.resolutions)
        for dpi in sorted_dpis:
            if 75 <= dpi <= 150:
                return dpi
        return sorted_dpis[0]


@dataclass(frozen=True, slots=True)
class ScannerCapabilities:
    """Parsed `/eSCL/ScannerCapabilities` representation."""

    make_and_model: str = ""
    serial_number: str = ""
    manufacturer: str = ""
    version: str = ""
    platen: InputSourceCaps | None = None
    adf_simplex: InputSourceCaps | None = None
    adf_duplex: InputSourceCaps | None = None
    extra_metadata: dict[str, str] = field(default_factory=dict)

    @property
    def is_brother(self) -> bool:
        """Return True if the capabilities indicate a Brother scanner."""
        combined = f"{self.make_and_model} {self.manufacturer}".lower()
        return "brother" in combined

    @property
    def available_sources(self) -> tuple[InputSourceCaps, ...]:
        """Return all supported input sources in priority order (Platen first)."""
        sources: list[InputSourceCaps] = []
        if self.platen is not None:
            sources.append(self.platen)
        if self.adf_simplex is not None:
            sources.append(self.adf_simplex)
        if self.adf_duplex is not None:
            sources.append(self.adf_duplex)
        return tuple(sources)

    @property
    def primary_source(self) -> InputSourceCaps:
        """Return the primary input source (Platen if present, else first ADF, else default)."""
        if self.platen is not None:
            return self.platen
        if self.adf_simplex is not None:
            return self.adf_simplex
        if self.adf_duplex is not None:
            return self.adf_duplex
        return InputSourceCaps(source=InputSourceKind.PLATEN)


@dataclass
class _ProfileData:
    resolutions: set[int] = field(default_factory=set)
    color_modes: list[str] = field(default_factory=list)
    document_formats: list[str] = field(default_factory=list)


def _extract_profile_data(profile_elem: etree._Element) -> _ProfileData:
    """Extract resolutions, color modes, and document formats from a `SettingProfile` element."""
    data = _ProfileData()

    # ColorModes
    for cm in _find_descendants(profile_elem, "ColorMode"):
        if cm.text and cm.text.strip() and cm.text.strip() not in data.color_modes:
            data.color_modes.append(cm.text.strip())

    # DocumentFormats (both pwg:DocumentFormat and scan:DocumentFormatExt)
    for tag_name in ("DocumentFormat", "DocumentFormatExt"):
        for df in _find_descendants(profile_elem, tag_name):
            if df.text and df.text.strip() and df.text.strip() not in data.document_formats:
                data.document_formats.append(df.text.strip())

    # DiscreteResolutions
    for disc in _find_descendants(profile_elem, "DiscreteResolution"):
        x_res = _child_int(disc, "XResolution", 0)
        y_res = _child_int(disc, "YResolution", 0)
        if x_res > 0 and (y_res == 0 or x_res == y_res):
            data.resolutions.add(x_res)
        elif x_res > 0 and y_res > 0:
            data.resolutions.add(min(x_res, y_res))

    # ResolutionRange (Min / Max / Step)
    for res_range in _find_descendants(profile_elem, "ResolutionRange"):
        x_range = _find_child(res_range, "XResolutionRange")
        target = x_range if x_range is not None else res_range
        min_dpi = _child_int(target, "Min", 75)
        max_dpi = _child_int(target, "Max", 600)
        for candidate in STANDARD_CANDIDATE_DPIS:
            if min_dpi <= candidate <= max_dpi:
                data.resolutions.add(candidate)
        if min_dpi > 0:
            data.resolutions.add(min_dpi)
        if max_dpi > 0:
            data.resolutions.add(max_dpi)

    return data


def _parse_input_source_caps(
    caps_elem: etree._Element,
    source_kind: InputSourceKind,
    shared_profiles: dict[str, _ProfileData],
) -> InputSourceCaps:
    """Parse `PlatenInputCaps`, `AdfSimplexInputCaps`, or `AdfDuplexInputCaps`."""
    min_width = _child_int(caps_elem, "MinWidth", 0)
    max_width = _child_int(caps_elem, "MaxWidth", 2550)
    min_height = _child_int(caps_elem, "MinHeight", 0)
    max_height = _child_int(caps_elem, "MaxHeight", 3508)
    max_opt_x = _child_int(caps_elem, "MaxOpticalXResolution", 0) or None
    max_opt_y = _child_int(caps_elem, "MaxOpticalYResolution", 0) or None

    resolutions: set[int] = set()
    color_modes: list[str] = []
    doc_formats: list[str] = []

    # 1. Inline SettingProfile elements inside InputCaps
    for inline_prof in _find_descendants(caps_elem, "SettingProfile"):
        pdata = _extract_profile_data(inline_prof)
        resolutions.update(pdata.resolutions)
        for cm in pdata.color_modes:
            if cm not in color_modes:
                color_modes.append(cm)
        for df in pdata.document_formats:
            if df not in doc_formats:
                doc_formats.append(df)

    # 2. Referenced SettingProfileRef elements (used by Brother & some eSCL implementations)
    for ref_elem in _find_descendants(caps_elem, "SettingProfileRef"):
        ref_name = (ref_elem.text or "").strip()
        if ref_name and ref_name in shared_profiles:
            pdata = shared_profiles[ref_name]
            resolutions.update(pdata.resolutions)
            for cm in pdata.color_modes:
                if cm not in color_modes:
                    color_modes.append(cm)
            for df in pdata.document_formats:
                if df not in doc_formats:
                    doc_formats.append(df)

    # 3. Direct elements on caps_elem itself (fallback for non-nested vendor XMLs)
    direct_data = _extract_profile_data(caps_elem)
    resolutions.update(direct_data.resolutions)
    for cm in direct_data.color_modes:
        if cm not in color_modes:
            color_modes.append(cm)
    for df in direct_data.document_formats:
        if df not in doc_formats:
            doc_formats.append(df)

    sorted_resolutions = tuple(sorted(resolutions)) if resolutions else (100, 150, 200, 300, 600)
    final_color_modes = tuple(color_modes) if color_modes else ("RGB24", "Grayscale8")
    final_formats = tuple(doc_formats) if doc_formats else ("image/jpeg", "application/pdf")

    return InputSourceCaps(
        source=source_kind,
        min_width=min_width,
        max_width=max_width,
        min_height=min_height,
        max_height=max_height,
        resolutions=sorted_resolutions,
        color_modes=final_color_modes,
        document_formats=final_formats,
        max_optical_x_resolution=max_opt_x,
        max_optical_y_resolution=max_opt_y,
    )


def parse_scanner_capabilities_xml(xml_content: bytes | str) -> ScannerCapabilities:
    """Parse `/eSCL/ScannerCapabilities` XML bytes or string into `ScannerCapabilities`."""
    raw_bytes = xml_content.encode("utf-8") if isinstance(xml_content, str) else xml_content
    parser = etree.XMLParser(recover=True, resolve_entities=False)
    root = etree.fromstring(raw_bytes, parser=parser)
    if root is None:
        raise ValueError("Invalid or empty ScannerCapabilities XML")

    make_and_model = _child_text(root, "MakeAndModel")
    serial_number = _child_text(root, "SerialNumber")
    manufacturer = _child_text(root, "Manufacturer")
    version = _child_text(root, "Version")

    # Collect top-level SettingProfiles keyed by `name` attribute (if any)
    shared_profiles: dict[str, _ProfileData] = {}
    for prof_elem in _find_descendants(root, "SettingProfile"):
        prof_name = prof_elem.attrib.get("name", "").strip()
        if prof_name:
            shared_profiles[prof_name] = _extract_profile_data(prof_elem)

    # Platen capabilities
    platen_caps: InputSourceCaps | None = None
    platen_elems = _find_descendants(root, "PlatenInputCaps")
    if platen_elems:
        platen_caps = _parse_input_source_caps(
            platen_elems[0],
            InputSourceKind.PLATEN,
            shared_profiles,
        )

    # ADF Simplex & Duplex capabilities
    adf_simplex_caps: InputSourceCaps | None = None
    simplex_elems = _find_descendants(root, "AdfSimplexInputCaps")
    if simplex_elems:
        adf_simplex_caps = _parse_input_source_caps(
            simplex_elems[0],
            InputSourceKind.ADF_SIMPLEX,
            shared_profiles,
        )

    adf_duplex_caps: InputSourceCaps | None = None
    duplex_elems = _find_descendants(root, "AdfDuplexInputCaps")
    if duplex_elems:
        adf_duplex_caps = _parse_input_source_caps(
            duplex_elems[0],
            InputSourceKind.ADF_DUPLEX,
            shared_profiles,
        )

    return ScannerCapabilities(
        make_and_model=make_and_model,
        serial_number=serial_number,
        manufacturer=manufacturer,
        version=version,
        platen=platen_caps,
        adf_simplex=adf_simplex_caps,
        adf_duplex=adf_duplex_caps,
    )


def fetch_scanner_capabilities(
    capabilities_url: str,
    *,
    timeout_seconds: float = 5.0,
    verify_tls: bool = False,
) -> ScannerCapabilities:
    """Fetch `/eSCL/ScannerCapabilities` over HTTP/HTTPS and parse the XML response."""
    with httpx.Client(timeout=timeout_seconds, verify=verify_tls, follow_redirects=True) as client:
        response = client.get(capabilities_url)
        response.raise_for_status()
        return parse_scanner_capabilities_xml(response.content)
