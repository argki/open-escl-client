"""eSCL `/eSCL/ScanJobs` XML builder and scan job execution client."""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, urlunparse
import httpx
from lxml import etree

from open_escl_client.escl.capabilities import InputSourceKind

PWG_NS = "http://www.pwg.org/schemas/2010/12/sm"
SCAN_NS = "http://schemas.hp.com/imaging/escl/2011/05/03"
NSMAP = {"scan": SCAN_NS, "pwg": PWG_NS}


class EsclScanError(RuntimeError):
    """Raised when an eSCL scan job creation or document retrieval fails."""


@dataclass(frozen=True, slots=True)
class EsclScanRequest:
    """Parameters for an eSCL `/eSCL/ScanJobs` request (coordinates in 1/300 inch)."""

    width_units: int
    height_units: int
    x_offset_units: int = 0
    y_offset_units: int = 0
    resolution_dpi: int = 100
    input_source: InputSourceKind = InputSourceKind.PLATEN
    color_mode: str = "RGB24"
    document_format: str = "image/jpeg"
    version: str = "2.0"


@dataclass(frozen=True, slots=True)
class EsclScanResult:
    """Result of a completed eSCL scan job."""

    image_bytes: bytes
    content_type: str
    job_url: str
    request: EsclScanRequest


def build_scan_settings_xml(request: EsclScanRequest) -> bytes:
    """Build a compact single-line eSCL `scan:ScanSettings` XML payload.

    Brother and other embedded eSCL firmware XML parsers can ignore region/resolution tags when
    pretty-printed indentation/newlines or incompatible element ordering are present. This builder
    emits a compact single-line XML matching the standard eSCL client structure.
    """
    root = etree.Element(f"{{{SCAN_NS}}}ScanSettings", nsmap=NSMAP)

    ver_elem = etree.SubElement(root, f"{{{PWG_NS}}}Version")
    ver_elem.text = request.version

    regions_elem = etree.SubElement(
        root,
        f"{{{PWG_NS}}}ScanRegions",
        attrib={f"{{{PWG_NS}}}MustHonor": "false"},
    )
    region_elem = etree.SubElement(regions_elem, f"{{{PWG_NS}}}ScanRegion")

    units_elem = etree.SubElement(region_elem, f"{{{PWG_NS}}}ContentRegionUnits")
    units_elem.text = "escl:ThreeHundredthsOfInches"

    width_elem = etree.SubElement(region_elem, f"{{{PWG_NS}}}Width")
    width_elem.text = str(max(1, int(request.width_units)))

    height_elem = etree.SubElement(region_elem, f"{{{PWG_NS}}}Height")
    height_elem.text = str(max(1, int(request.height_units)))

    x_off_elem = etree.SubElement(region_elem, f"{{{PWG_NS}}}XOffset")
    x_off_elem.text = str(max(0, int(request.x_offset_units)))

    y_off_elem = etree.SubElement(region_elem, f"{{{PWG_NS}}}YOffset")
    y_off_elem.text = str(max(0, int(request.y_offset_units)))

    format_elem = etree.SubElement(root, f"{{{PWG_NS}}}DocumentFormat")
    format_elem.text = request.document_format

    input_src_elem = etree.SubElement(root, f"{{{PWG_NS}}}InputSource")
    if request.input_source == InputSourceKind.PLATEN:
        input_src_elem.text = "Platen"
    else:
        input_src_elem.text = "Feeder"

    color_elem = etree.SubElement(root, f"{{{SCAN_NS}}}ColorMode")
    color_elem.text = request.color_mode

    x_res_elem = etree.SubElement(root, f"{{{SCAN_NS}}}XResolution")
    x_res_elem.text = str(int(request.resolution_dpi))

    y_res_elem = etree.SubElement(root, f"{{{SCAN_NS}}}YResolution")
    y_res_elem.text = str(int(request.resolution_dpi))

    if request.input_source != InputSourceKind.PLATEN:
        duplex_elem = etree.SubElement(root, f"{{{SCAN_NS}}}Duplex")
        duplex_elem.text = "true" if request.input_source == InputSourceKind.ADF_DUPLEX else "false"

    body = etree.tostring(root, xml_declaration=False, encoding="UTF-8", pretty_print=False)
    return b'<?xml version="1.0" encoding="UTF-8"?>' + body


def resolve_job_url(base_url: str, location_header: str) -> str:
    """Resolve the `Location` header returned by `POST /eSCL/ScanJobs` against `base_url`.

    Preserves the host/IP and port of `base_url` in case the scanner returns an mDNS `.local`
    hostname or internal IP in the `Location` header.
    """
    cleaned_loc = location_header.strip()
    if not cleaned_loc:
        raise EsclScanError("Scanner did not return a Location header for ScanJobs")

    base_parsed = urlparse(base_url.rstrip("/") + "/")
    joined = urljoin(base_url.rstrip("/") + "/", cleaned_loc)
    joined_parsed = urlparse(joined)

    # Keep the scheme and netloc of base_url so direct IP connections never switch to unresolvable hostnames
    normalized = urlunparse(
        (
            base_parsed.scheme,
            base_parsed.netloc,
            joined_parsed.path.rstrip("/"),
            joined_parsed.params,
            joined_parsed.query,
            joined_parsed.fragment,
        )
    )
    return normalized


class EsclScanClient:
    """HTTP/HTTPS client for executing eSCL scan jobs (`/eSCL/ScanJobs`)."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 45.0,
        verify_tls: bool = False,
        max_busy_retries: int = 5,
        retry_delay_seconds: float = 0.6,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.verify_tls = verify_tls
        self.max_busy_retries = max_busy_retries
        self.retry_delay_seconds = retry_delay_seconds

    @property
    def scan_jobs_url(self) -> str:
        return f"{self.base_url}/ScanJobs"

    def create_scan_job(self, client: httpx.Client, request: EsclScanRequest) -> str:
        """Send `POST /eSCL/ScanJobs` and return the resolved job URL."""
        xml_payload = build_scan_settings_xml(request)
        headers = {"Content-Type": "text/xml"}

        for attempt in range(self.max_busy_retries + 1):
            response = client.post(self.scan_jobs_url, content=xml_payload, headers=headers)
            if response.status_code == 503 and attempt < self.max_busy_retries:
                time.sleep(self.retry_delay_seconds)
                continue
            if response.status_code not in (200, 201):
                raise EsclScanError(
                    f"ScanJobs creation failed with HTTP {response.status_code}: {response.text[:200]}"
                )
            location = response.headers.get("Location") or response.headers.get("location") or ""
            return resolve_job_url(self.base_url, location)

        raise EsclScanError("Scanner remained busy (HTTP 503) while creating scan job")

    def fetch_next_document(self, client: httpx.Client, job_url: str) -> tuple[bytes, str]:
        """Fetch `{job_url}/NextDocument` and return `(image_bytes, content_type)`."""
        next_doc_url = f"{job_url.rstrip('/')}/NextDocument"
        for attempt in range(self.max_busy_retries + 1):
            response = client.get(next_doc_url)
            if response.status_code == 503 and attempt < self.max_busy_retries:
                time.sleep(self.retry_delay_seconds)
                continue
            if response.status_code != 200:
                raise EsclScanError(
                    f"NextDocument retrieval failed with HTTP {response.status_code}"
                )
            content_type = response.headers.get("Content-Type", "image/jpeg")
            if not response.content:
                raise EsclScanError("Scanner returned empty document payload")
            return response.content, content_type

        raise EsclScanError("Scanner remained busy (HTTP 503) while fetching NextDocument")

    def cancel_or_cleanup_job(self, client: httpx.Client, job_url: str) -> None:
        """Send `DELETE {job_url}` to clean up or cancel an eSCL scan job (ignoring 404/405)."""
        try:
            client.delete(job_url.rstrip("/"))
        except Exception:
            pass

    def scan_single_page(self, request: EsclScanRequest) -> EsclScanResult:
        """Create a scan job, download the scanned page, and clean up the job slot."""
        with httpx.Client(
            timeout=self.timeout_seconds,
            verify=self.verify_tls,
            follow_redirects=True,
        ) as client:
            job_url = self.create_scan_job(client, request)
            try:
                image_bytes, content_type = self.fetch_next_document(client, job_url)
            finally:
                self.cancel_or_cleanup_job(client, job_url)

            return EsclScanResult(
                image_bytes=image_bytes,
                content_type=content_type,
                job_url=job_url,
                request=request,
            )
