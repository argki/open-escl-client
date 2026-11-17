"""Unit tests for mDNS eSCL scanner discovery."""

from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch
from zeroconf import ServiceInfo

from open_escl_client.escl.discovery import (
    ESCL_HTTP_SERVICE_TYPE,
    ESCL_HTTPS_SERVICE_TYPE,
    DiscoveredScanner,
    EsclScannerDiscovery,
    _EsclServiceListener,
    deduplicate_and_sort_scanners,
)


def _make_service_info(
    *,
    service_type: str = ESCL_HTTP_SERVICE_TYPE,
    instance_name: str = "Brother MFC-L2750DW series",
    server: str = "BRW001122334455.local.",
    ip: str = "192.168.1.50",
    port: int = 80,
    properties: dict[bytes, bytes] | None = None,
) -> ServiceInfo:
    full_name = f"{instance_name}.{service_type}"
    props = properties or {
        b"txtvers": b"1",
        b"ty": instance_name.encode("utf-8"),
        b"rs": b"eSCL",
        b"uuid": b"564e4234-3030-3130-3832-001122334455",
        b"note": b"Office",
        b"adminurl": b"http://BRW001122334455.local./net/net/airprint.html",
    }
    return ServiceInfo(
        type_=service_type,
        name=full_name,
        addresses=[socket.inet_aton(ip)],
        port=port,
        properties=props,
        server=server,
    )


def test_discovered_scanner_from_brother_service_info() -> None:
    info = _make_service_info()
    scanner = DiscoveredScanner.from_service_info(
        ESCL_HTTP_SERVICE_TYPE,
        f"Brother MFC-L2750DW series.{ESCL_HTTP_SERVICE_TYPE}",
        info,
    )

    assert scanner.name == "Brother MFC-L2750DW series"
    assert scanner.is_brother is True
    assert scanner.scheme == "http"
    assert scanner.port == 80
    assert scanner.resource_path == "eSCL"
    assert scanner.base_url == "http://192.168.1.50:80/eSCL"
    assert scanner.capabilities_url == "http://192.168.1.50:80/eSCL/ScannerCapabilities"
    assert "192.168.1.50" in scanner.display_label


def test_deduplicate_and_sort_prioritizes_brother_and_http() -> None:
    generic_scanner = DiscoveredScanner(
        name="Generic AirScan Unit",
        host="generic.local",
        port=80,
        scheme="http",
        uuid="uuid-generic-1",
        addresses=("192.168.1.90",),
    )
    brother_https = DiscoveredScanner(
        name="Brother DCP-J926N",
        host="brother.local",
        port=443,
        scheme="https",
        uuid="uuid-brother-1",
        addresses=("192.168.1.25",),
    )
    brother_http = DiscoveredScanner(
        name="Brother DCP-J926N",
        host="brother.local",
        port=80,
        scheme="http",
        uuid="uuid-brother-1",
        addresses=("192.168.1.25",),
    )

    result = deduplicate_and_sort_scanners([generic_scanner, brother_https, brother_http])
    assert len(result) == 2
    # Brother scanner must come first
    assert result[0].name == "Brother DCP-J926N"
    assert result[0].scheme == "http"
    assert result[1].name == "Generic AirScan Unit"


def test_service_listener_add_and_remove() -> None:
    listener = _EsclServiceListener()
    fake_zc = MagicMock()
    info = _make_service_info(
        service_type=ESCL_HTTPS_SERVICE_TYPE,
        instance_name="Brother ADS-2800W",
        port=443,
    )
    service_name = f"Brother ADS-2800W.{ESCL_HTTPS_SERVICE_TYPE}"
    fake_zc.get_service_info.return_value = info

    listener.add_service(fake_zc, ESCL_HTTPS_SERVICE_TYPE, service_name)
    assert service_name in listener.discovered
    assert listener.discovered[service_name].scheme == "https"
    assert listener.discovered[service_name].is_brother is True

    listener.remove_service(fake_zc, ESCL_HTTPS_SERVICE_TYPE, service_name)
    assert service_name not in listener.discovered


def test_escl_scanner_discovery_with_mocked_zeroconf() -> None:
    info = _make_service_info()
    service_name = f"Brother MFC-L2750DW series.{ESCL_HTTP_SERVICE_TYPE}"

    with patch("open_escl_client.escl.discovery.Zeroconf") as mock_zc_cls, patch(
        "open_escl_client.escl.discovery.ServiceBrowser"
    ) as mock_browser_cls:
        mock_zc = MagicMock()
        mock_zc.get_service_info.return_value = info
        mock_zc_cls.return_value = mock_zc

        def _trigger_add(zc, service_type, listener):
            if service_type == ESCL_HTTP_SERVICE_TYPE:
                listener.add_service(zc, service_type, service_name)
            return MagicMock()

        mock_browser_cls.side_effect = _trigger_add

        discovery = EsclScannerDiscovery(timeout_seconds=0.05)
        scanners = discovery.discover()

        assert len(scanners) == 1
        assert scanners[0].name == "Brother MFC-L2750DW series"
        mock_zc.close.assert_called_once()
