"""Unit tests for `/eSCL/ScanJobs` XML builder and `EsclScanClient`."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from lxml import etree
import pytest

from open_escl_client.escl.capabilities import InputSourceKind
from open_escl_client.escl.scan_client import (
    EsclScanClient,
    EsclScanError,
    EsclScanRequest,
    build_scan_settings_xml,
    resolve_job_url,
)


def test_build_scan_settings_xml_platen_and_adf_duplex() -> None:
    req_platen = EsclScanRequest(
        width_units=2480,
        height_units=3508,
        resolution_dpi=100,
        input_source=InputSourceKind.PLATEN,
        color_mode="RGB24",
        document_format="image/jpeg",
    )
    xml_bytes = build_scan_settings_xml(req_platen)
    root = etree.fromstring(xml_bytes)
    xml_text = xml_bytes.decode("utf-8")

    assert root.tag.endswith("ScanSettings")
    assert "<pwg:Width>2480</pwg:Width>" in xml_text
    assert "<pwg:Height>3508</pwg:Height>" in xml_text
    assert "<pwg:InputSource>Platen</pwg:InputSource>" in xml_text
    assert "<scan:XResolution>100</scan:XResolution>" in xml_text
    assert "<scan:YResolution>100</scan:YResolution>" in xml_text
    assert "Duplex" not in xml_text

    req_duplex = EsclScanRequest(
        width_units=2480,
        height_units=3508,
        resolution_dpi=300,
        input_source=InputSourceKind.ADF_DUPLEX,
    )
    xml_duplex = build_scan_settings_xml(req_duplex).decode("utf-8")
    assert "<pwg:InputSource>Feeder</pwg:InputSource>" in xml_duplex
    assert "<scan:Duplex>true</scan:Duplex>" in xml_duplex


def test_resolve_job_url_preserves_base_host_and_port() -> None:
    base = "http://192.168.1.50:80/eSCL"
    # Relative Location header
    assert (
        resolve_job_url(base, "/eSCL/ScanJobs/42")
        == "http://192.168.1.50:80/eSCL/ScanJobs/42"
    )
    # Full URL with Brother .local hostname -> rewritten to keep connected IP:port
    assert (
        resolve_job_url(base, "http://BRW001122334455.local.:80/eSCL/ScanJobs/99")
        == "http://192.168.1.50:80/eSCL/ScanJobs/99"
    )


def test_escl_scan_client_single_page_success_and_cleanup() -> None:
    fake_jpeg = b"\xff\xd8\xff\xe0FAKE_JPEG_DATA\xff\xd9"
    post_resp = MagicMock()
    post_resp.status_code = 201
    post_resp.headers = {"Location": "/eSCL/ScanJobs/job-1"}

    get_resp = MagicMock()
    get_resp.status_code = 200
    get_resp.headers = {"Content-Type": "image/jpeg"}
    get_resp.content = fake_jpeg

    mock_http = MagicMock()
    mock_http.post.return_value = post_resp
    mock_http.get.return_value = get_resp

    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = mock_http

    with patch("open_escl_client.escl.scan_client.httpx.Client", return_value=mock_ctx):
        client = EsclScanClient("http://192.168.1.50:80/eSCL")
        req = EsclScanRequest(width_units=2480, height_units=3508, resolution_dpi=100)
        result = client.scan_single_page(req)

        assert result.image_bytes == fake_jpeg
        assert result.content_type == "image/jpeg"
        assert result.job_url == "http://192.168.1.50:80/eSCL/ScanJobs/job-1"
        mock_http.post.assert_called_once()
        mock_http.get.assert_called_once_with(
            "http://192.168.1.50:80/eSCL/ScanJobs/job-1/NextDocument"
        )
        mock_http.delete.assert_called_once_with(
            "http://192.168.1.50:80/eSCL/ScanJobs/job-1"
        )


def test_escl_scan_client_retries_on_503_busy() -> None:
    busy_resp = MagicMock(status_code=503, text="Scanner Busy")
    ok_post = MagicMock(status_code=201, headers={"Location": "/eSCL/ScanJobs/job-2"})
    ok_get = MagicMock(
        status_code=200,
        headers={"Content-Type": "image/jpeg"},
        content=b"\xff\xd8\xff\xd9",
    )

    mock_http = MagicMock()
    mock_http.post.side_effect = [busy_resp, ok_post]
    mock_http.get.side_effect = [busy_resp, ok_get]

    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = mock_http

    with patch("open_escl_client.escl.scan_client.httpx.Client", return_value=mock_ctx):
        client = EsclScanClient(
            "http://192.168.1.50:80/eSCL",
            retry_delay_seconds=0.01,
        )
        req = EsclScanRequest(width_units=2480, height_units=3508, resolution_dpi=100)
        result = client.scan_single_page(req)
        assert result.image_bytes == b"\xff\xd8\xff\xd9"
        assert mock_http.post.call_count == 2
        assert mock_http.get.call_count == 2


def test_escl_scan_client_raises_on_http_error() -> None:
    err_post = MagicMock(status_code=409, text="Conflict")
    mock_http = MagicMock()
    mock_http.post.return_value = err_post
    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = mock_http

    with patch("open_escl_client.escl.scan_client.httpx.Client", return_value=mock_ctx):
        client = EsclScanClient("http://192.168.1.50:80/eSCL")
        req = EsclScanRequest(width_units=2480, height_units=3508, resolution_dpi=100)
        with pytest.raises(EsclScanError, match="HTTP 409"):
            client.scan_single_page(req)
