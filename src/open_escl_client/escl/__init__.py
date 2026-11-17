"""eSCL (AirScan) protocol client, discovery, and capability parsers."""

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
    "deduplicate_and_sort_scanners",
]
