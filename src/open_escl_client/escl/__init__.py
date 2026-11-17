"""eSCL (AirScan) protocol client, discovery, and capability parsers."""

from open_escl_client.escl.capabilities import (
    InputSourceCaps,
    InputSourceKind,
    ScannerCapabilities,
    fetch_scanner_capabilities,
    mm_to_three_hundredths_inch,
    parse_scanner_capabilities_xml,
    three_hundredths_inch_to_mm,
)
from open_escl_client.escl.discovery import (
    ESCL_HTTP_SERVICE_TYPE,
    ESCL_HTTPS_SERVICE_TYPE,
    DiscoveredScanner,
    EsclScannerDiscovery,
    deduplicate_and_sort_scanners,
)

__all__ = [
    "ESCL_HTTP_SERVICE_TYPE",
    "ESCL_HTTPS_SERVICE_TYPE",
    "DiscoveredScanner",
    "EsclScannerDiscovery",
    "InputSourceCaps",
    "InputSourceKind",
    "ScannerCapabilities",
    "deduplicate_and_sort_scanners",
    "fetch_scanner_capabilities",
    "mm_to_three_hundredths_inch",
    "parse_scanner_capabilities_xml",
    "three_hundredths_inch_to_mm",
]
