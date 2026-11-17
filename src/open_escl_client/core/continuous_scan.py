"""Continuous scan session inheriting preview settings for rapid repeated scanning and saving."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from open_escl_client.config.user_config import normalize_save_format
from open_escl_client.core.exporter import ExportOptions, ExportResult, ImageExporter
from open_escl_client.core.naming import FileNamingEngine
from open_escl_client.core.paper_size import PaperSizeOption
from open_escl_client.core.preview_service import PreviewSession
from open_escl_client.escl.capabilities import InputSourceCaps
from open_escl_client.escl.scan_client import EsclScanClient, EsclScanRequest

PostProcessor = Callable[[bytes], list[bytes]]


@dataclass(frozen=True, slots=True)
class ScanSaveProfile:
    """Locked-in scan and save configuration reused across consecutive scans."""

    base_url: str
    source_caps: InputSourceCaps
    paper_size: PaperSizeOption
    save_dir: Path
    naming_template: str = "Scan_{date}_{seq:03d}"
    file_format: str = "PDF"
    resolution_dpi: int = 300
    quality: int = 85
    color_mode: str = "RGB24"
    multipage_pdf: bool = False
    post_processor: PostProcessor | None = None

    @property
    def is_multipage_pdf_mode(self) -> bool:
        return self.multipage_pdf and normalize_save_format(self.file_format) == "PDF"


@dataclass(frozen=True, slots=True)
class ContinuousScanStepResult:
    """Outcome of a single scan step within a `ContinuousScanSession`."""

    saved_results: tuple[ExportResult, ...]
    buffered_page_count: int
    total_saved_files: int
    next_seq: int
    last_image_bytes: bytes


class ContinuousScanSession:
    """Maintains locked-in scan settings, sequence numbering, and optional multi-page PDF buffer."""

    def __init__(
        self,
        profile: ScanSaveProfile,
        *,
        scan_client_factory: Callable[[str], EsclScanClient] | None = None,
        naming_engine: FileNamingEngine | None = None,
        exporter: ImageExporter | None = None,
        initial_seq: int = 1,
    ) -> None:
        self._profile = profile
        self._scan_client_factory = scan_client_factory or (lambda url: EsclScanClient(url))
        self._naming_engine = naming_engine or FileNamingEngine()
        self._exporter = exporter or ImageExporter()
        self._next_seq: int = max(1, int(initial_seq))
        self._saved_results: list[ExportResult] = []
        self._pending_pages: list[bytes] = []

    @classmethod
    def from_preview_session(
        cls,
        preview_session: PreviewSession,
        *,
        base_url: str,
        source_caps: InputSourceCaps,
        save_dir: Path | str,
        naming_template: str = "Scan_{date}_{seq:03d}",
        file_format: str = "PDF",
        resolution_dpi: int = 300,
        quality: int = 85,
        color_mode: str = "RGB24",
        multipage_pdf: bool = False,
        post_processor: PostProcessor | None = None,
        scan_client_factory: Callable[[str], EsclScanClient] | None = None,
        naming_engine: FileNamingEngine | None = None,
        exporter: ImageExporter | None = None,
        initial_seq: int = 1,
    ) -> ContinuousScanSession:
        """Create a continuous scan session inheriting paper size & input source from a `PreviewSession`."""
        profile = ScanSaveProfile(
            base_url=base_url,
            source_caps=source_caps,
            paper_size=preview_session.paper_size,
            save_dir=Path(save_dir).expanduser(),
            naming_template=naming_template,
            file_format=file_format,
            resolution_dpi=resolution_dpi,
            quality=quality,
            color_mode=color_mode,
            multipage_pdf=multipage_pdf,
            post_processor=post_processor,
        )
        return cls(
            profile,
            scan_client_factory=scan_client_factory,
            naming_engine=naming_engine,
            exporter=exporter,
            initial_seq=initial_seq,
        )

    @property
    def profile(self) -> ScanSaveProfile:
        return self._profile

    @property
    def next_seq(self) -> int:
        return self._next_seq

    @property
    def saved_results(self) -> tuple[ExportResult, ...]:
        return tuple(self._saved_results)

    @property
    def pending_page_count(self) -> int:
        return len(self._pending_pages)

    def update_profile(self, profile: ScanSaveProfile) -> None:
        """Update the active scan/save profile while preserving sequence progress."""
        self._profile = profile

    def clear_pending_pages(self) -> None:
        """Discard any buffered pages waiting for multi-page PDF finalization."""
        self._pending_pages.clear()

    def reset_counters(self, initial_seq: int = 1) -> None:
        """Reset sequence counter, saved history, and pending buffer."""
        self._next_seq = max(1, int(initial_seq))
        self._saved_results.clear()
        self._pending_pages.clear()

    def build_main_scan_request(self) -> EsclScanRequest:
        """Build the high-DPI eSCL scan request from the active `ScanSaveProfile`."""
        caps = self._profile.source_caps
        chosen_color = (
            self._profile.color_mode
            if self._profile.color_mode in caps.color_modes
            else caps.color_modes[0]
        )
        return EsclScanRequest(
            width_units=self._profile.paper_size.width_units,
            height_units=self._profile.paper_size.height_units,
            x_offset_units=0,
            y_offset_units=0,
            resolution_dpi=self._profile.resolution_dpi,
            input_source=caps.source,
            color_mode=chosen_color,
            document_format="image/jpeg",
        )

    def preview_next_filename(self, now: datetime | None = None) -> str:
        """Preview the filename that will be generated for the next saved file."""
        return self._naming_engine.preview_next_filename(
            self._profile.save_dir,
            self._profile.naming_template,
            file_format=self._profile.file_format,
            start_seq=self._next_seq,
            now=now,
        )

    def scan_and_save_next(self, now: datetime | None = None) -> ContinuousScanStepResult:
        """Execute one main scan using the locked-in settings and save (or buffer for multi-page PDF)."""
        request = self.build_main_scan_request()
        client = self._scan_client_factory(self._profile.base_url)
        scan_result = client.scan_single_page(request)

        pages = (
            self._profile.post_processor(scan_result.image_bytes)
            if self._profile.post_processor is not None
            else [scan_result.image_bytes]
        )
        if not pages:
            pages = [scan_result.image_bytes]

        if self._profile.is_multipage_pdf_mode:
            self._pending_pages.extend(pages)
            return ContinuousScanStepResult(
                saved_results=(),
                buffered_page_count=len(self._pending_pages),
                total_saved_files=len(self._saved_results),
                next_seq=self._next_seq,
                last_image_bytes=pages[-1],
            )

        export_opts = ExportOptions(
            file_format=self._profile.file_format,
            dpi=self._profile.resolution_dpi,
            quality=self._profile.quality,
        )
        step_saved: list[ExportResult] = []
        for page_bytes in pages:
            out_path, used_seq = self._naming_engine.resolve_next_path(
                self._profile.save_dir,
                self._profile.naming_template,
                file_format=self._profile.file_format,
                start_seq=self._next_seq,
                now=now,
            )
            exported = self._exporter.export_single(page_bytes, out_path, options=export_opts)
            self._saved_results.append(exported)
            step_saved.append(exported)
            self._next_seq = used_seq + 1

        return ContinuousScanStepResult(
            saved_results=tuple(step_saved),
            buffered_page_count=0,
            total_saved_files=len(self._saved_results),
            next_seq=self._next_seq,
            last_image_bytes=pages[-1],
        )

    def finalize_multipage_pdf(self, now: datetime | None = None) -> ExportResult:
        """Write all buffered pages into a single multi-page PDF and clear the buffer."""
        if not self._pending_pages:
            raise ValueError("結合するスキャン済みページがありません")

        out_path, used_seq = self._naming_engine.resolve_next_path(
            self._profile.save_dir,
            self._profile.naming_template,
            file_format="PDF",
            start_seq=self._next_seq,
            now=now,
        )
        export_opts = ExportOptions(
            file_format="PDF",
            dpi=self._profile.resolution_dpi,
            quality=self._profile.quality,
        )
        exported = self._exporter.export_pages(self._pending_pages, out_path, options=export_opts)
        self._pending_pages.clear()
        self._saved_results.append(exported)
        self._next_seq = used_seq + 1
        return exported
