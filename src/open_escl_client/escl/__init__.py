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
from open_escl_client.escl.scan_client import (
    EsclScanClient,
    EsclScanError,
    EsclScanRequest,
    EsclScanResult,
    build_scan_settings_xml,
    resolve_job_url,
)

__all__ = [
    "ESCL_HTTP_SERVICE_TYPE",
    "ESCL_HTTPS_SERVICE_TYPE",
    "DiscoveredScanner",
    "EsclScanClient",
    "EsclScanError",
    "EsclScanRequest",
    "EsclScanResult",
    "EsclScannerDiscovery",
    "InputSourceCaps",
    "InputSourceKind",
    "ScannerCapabilities",
    "build_scan_settings_xml",
    "deduplicate_and_sort_scanners",
    "fetch_scanner_capabilities",
    "mm_to_three_hundredths_inch",
    "parse_scanner_capabilities_xml",
    "resolve_job_url",
    "three_hundredths_inch_to_mm",
]
