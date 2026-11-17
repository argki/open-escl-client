"""mDNS / Bonjour (`_uscan._tcp` and `_uscans._tcp`) scanner discovery for eSCL."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol
from zeroconf import ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf

ESCL_HTTP_SERVICE_TYPE = "_uscan._tcp.local."
ESCL_HTTPS_SERVICE_TYPE = "_uscans._tcp.local."
ESCL_SERVICE_TYPES: tuple[str, ...] = (
    ESCL_HTTP_SERVICE_TYPE,
    ESCL_HTTPS_SERVICE_TYPE,
)


def _decode_txt_properties(raw_properties: dict[bytes | str, bytes | str | None]) -> dict[str, str]:
    """Decode mDNS TXT record key-value pairs into strings."""
    decoded: dict[str, str] = {}
    for raw_key, raw_val in raw_properties.items():
        key = raw_key.decode("utf-8", errors="replace") if isinstance(raw_key, bytes) else str(raw_key)
        if raw_val is None:
            decoded[key] = ""
        elif isinstance(raw_val, bytes):
            decoded[key] = raw_val.decode("utf-8", errors="replace")
        else:
            decoded[key] = str(raw_val)
    return decoded


def _normalize_resource_path(rs_value: str) -> str:
    """Normalize the eSCL `rs` TXT attribute (defaulting to `eSCL`)."""
    cleaned = rs_value.strip().strip("/")
    return cleaned if cleaned else "eSCL"


@dataclass(frozen=True, slots=True)
class DiscoveredScanner:
    """Metadata and connection endpoint for an eSCL scanner found on the network."""

    name: str
    host: str
    port: int
    scheme: str = "http"
    resource_path: str = "eSCL"
    uuid: str = ""
    note: str = ""
    service_name: str = ""
    addresses: tuple[str, ...] = field(default_factory=tuple)
    txt_records: dict[str, str] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        """Return the base eSCL endpoint URL (e.g. `http://192.168.1.50:80/eSCL`)."""
        target_host = self.addresses[0] if self.addresses else self.host.rstrip(".")
        if ":" in target_host and not target_host.startswith("["):
            target_host = f"[{target_host}]"
        path = _normalize_resource_path(self.resource_path)
        return f"{self.scheme}://{target_host}:{self.port}/{path}"

    @property
    def capabilities_url(self) -> str:
        """Return the full `/ScannerCapabilities` URL."""
        return f"{self.base_url}/ScannerCapabilities"

    @property
    def is_brother(self) -> bool:
        """Return True if the scanner appears to be a Brother device."""
        haystack = f"{self.name} {self.service_name} {self.txt_records.get('ty', '')} {self.txt_records.get('adminurl', '')}".lower()
        return "brother" in haystack

    @property
    def display_label(self) -> str:
        """Human-friendly label for UI dropdowns."""
        target_host = self.addresses[0] if self.addresses else self.host.rstrip(".")
        prefix = "[Brother] " if self.is_brother and not self.name.lower().startswith("brother") else ""
        return f"{prefix}{self.name} ({target_host})"

    @classmethod
    def from_service_info(cls, service_type: str, service_name: str, info: ServiceInfo) -> DiscoveredScanner:
        """Construct a `DiscoveredScanner` from a Zeroconf `ServiceInfo`."""
        txt = _decode_txt_properties(info.properties or {})
        scheme = "https" if service_type.startswith("_uscans.") else "http"
        rs_path = _normalize_resource_path(txt.get("rs", "eSCL"))

        instance_label = service_name
        if service_name.endswith(f".{service_type}"):
            instance_label = service_name[: -(len(service_type) + 1)]

        model_name = txt.get("ty", "").strip() or instance_label
        server_host = (info.server or "").rstrip(".")
        parsed_addrs = tuple(info.parsed_scoped_addresses()) if hasattr(info, "parsed_scoped_addresses") else ()
        port = int(info.port or (443 if scheme == "https" else 80))

        return cls(
            name=model_name,
            host=server_host or (parsed_addrs[0] if parsed_addrs else "localhost"),
            port=port,
            scheme=scheme,
            resource_path=rs_path,
            uuid=txt.get("uuid", "").strip(),
            note=txt.get("note", "").strip(),
            service_name=service_name,
            addresses=parsed_addrs,
            txt_records=txt,
        )


class ZeroconfResolverProtocol(Protocol):
    """Protocol for Zeroconf instance to support dependency injection in tests."""

    def get_service_info(self, type_: str, name: str, timeout: int = 3000) -> ServiceInfo | None: ...
    def close(self) -> None: ...


class _EsclServiceListener(ServiceListener):
    """Internal Zeroconf listener collecting `_uscan._tcp` and `_uscans._tcp` services."""

    def __init__(self) -> None:
        self.discovered: dict[str, DiscoveredScanner] = {}

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._resolve_and_store(zc, type_, name)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self._resolve_and_store(zc, type_, name)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.discovered.pop(name, None)

    def _resolve_and_store(self, zc: ZeroconfResolverProtocol, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if info is None:
            return
        scanner = DiscoveredScanner.from_service_info(type_, name, info)
        self.discovered[name] = scanner


def deduplicate_and_sort_scanners(scanners: list[DiscoveredScanner]) -> list[DiscoveredScanner]:
    """Deduplicate scanners by UUID or host, prioritizing Brother scanners and HTTP/HTTPS properly."""
    by_key: dict[str, DiscoveredScanner] = {}
    for scanner in scanners:
        key = scanner.uuid or f"{scanner.addresses[0] if scanner.addresses else scanner.host}:{scanner.name}"
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = scanner
        else:
            # Prefer HTTP (`_uscan._tcp`) on local LAN to avoid self-signed TLS issues unless only HTTPS exists
            if existing.scheme == "https" and scanner.scheme == "http":
                by_key[key] = scanner

    # Sort Brother scanners first (priority target: Brother × macOS), then alphabetically by name
    return sorted(
        by_key.values(),
        key=lambda s: (not s.is_brother, s.name.lower(), s.host.lower()),
    )


class EsclScannerDiscovery:
    """Discover eSCL scanners on the local network via mDNS / Bonjour."""

    def __init__(self, timeout_seconds: float = 1.5) -> None:
        self.timeout_seconds = timeout_seconds

    def discover(self, timeout_seconds: float | None = None) -> list[DiscoveredScanner]:
        """Run mDNS discovery for `_uscan._tcp.local.` and `_uscans._tcp.local.`."""
        wait_time = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        listener = _EsclServiceListener()
        zc = Zeroconf()
        try:
            browsers = [ServiceBrowser(zc, service_type, listener) for service_type in ESCL_SERVICE_TYPES]
            time.sleep(max(0.05, wait_time))
            _ = browsers
        finally:
            zc.close()

        return deduplicate_and_sort_scanners(list(listener.discovered.values()))
