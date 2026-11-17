"""ViewModel bridging Python core logic and declarative QML UI."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import threading
from urllib.parse import urlparse

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from open_escl_client.config.user_config import (
    UserConfig,
    UserConfigStore,
    normalize_save_format,
)
from open_escl_client.core.continuous_scan import (
    ContinuousScanSession,
    ContinuousScanStepResult,
    ScanSaveProfile,
)
from open_escl_client.core.exporter import ImageExporter
from open_escl_client.core.naming import FileNamingEngine
from open_escl_client.core.paper_size import PaperSizeOption, PaperSizeResolver
from open_escl_client.core.preview_service import (
    PreviewScanService,
    PreviewSession,
    select_preview_dpi,
)
from open_escl_client.escl.capabilities import (
    InputSourceCaps,
    InputSourceKind,
    ScannerCapabilities,
    fetch_scanner_capabilities,
)
from open_escl_client.escl.discovery import DiscoveredScanner, EsclScannerDiscovery
from open_escl_client.escl.scan_client import EsclScanClient
from open_escl_client.ui.image_provider import PreviewImageProvider

INPUT_SOURCE_LABELS: dict[InputSourceKind, str] = {
    InputSourceKind.PLATEN: "原稿台 (Platen)",
    InputSourceKind.ADF_SIMPLEX: "ADF (片面)",
    InputSourceKind.ADF_DUPLEX: "ADF (両面)",
}

LABEL_TO_INPUT_SOURCE: dict[str, InputSourceKind] = {v: k for k, v in INPUT_SOURCE_LABELS.items()}

DEFAULT_PLATEN_CAPS = InputSourceCaps(
    source=InputSourceKind.PLATEN,
    min_width=96,
    max_width=2550,
    min_height=96,
    max_height=3508,
    resolutions=(75, 100, 150, 200, 300, 400, 600, 1200),
)


def _parse_manual_scanner_endpoint(raw_address: str) -> DiscoveredScanner:
    """Create a `DiscoveredScanner` from a user-provided IP address, hostname, or URL."""
    cleaned = raw_address.strip()
    if not cleaned:
        raise ValueError("IPアドレスまたはURLを入力してください")

    if "://" not in cleaned:
        cleaned = f"http://{cleaned}"

    parsed = urlparse(cleaned)
    scheme = parsed.scheme if parsed.scheme in ("http", "https") else "http"
    host = parsed.hostname or cleaned
    port = parsed.port or (443 if scheme == "https" else 80)
    path = parsed.path.strip("/") or "eSCL"
    if path.endswith("ScannerCapabilities"):
        path = path[: -len("ScannerCapabilities")].strip("/") or "eSCL"

    return DiscoveredScanner(
        name=f"eSCL Scanner ({host})",
        host=host,
        port=port,
        scheme=scheme,
        resource_path=path,
        addresses=(host,),
    )


class AppViewModel(QObject):
    """Main ViewModel exposed to QML as `viewModel`."""

    scannerChanged = Signal()
    inputSourceChanged = Signal()
    paperSizeChanged = Signal()
    resolutionChanged = Signal()
    saveFormatChanged = Signal()
    saveDirectoryChanged = Signal()
    namingRuleChanged = Signal()
    jpegQualityChanged = Signal()
    multipagePdfChanged = Signal()
    nextFilenamePreviewChanged = Signal()
    scanSaveChanged = Signal()
    statusMessageChanged = Signal()
    isBusyChanged = Signal()
    previewChanged = Signal()
    previewZoomChanged = Signal()

    # Internal thread-safe completion signals for background scan tasks
    _previewFinishedInternal = Signal(object)
    _previewFailedInternal = Signal(str)
    _scanSaveFinishedInternal = Signal(object)
    _scanSaveFailedInternal = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        discovery_service: EsclScannerDiscovery | None = None,
        capabilities_fetcher: Callable[[str], ScannerCapabilities] = fetch_scanner_capabilities,
        paper_size_resolver: PaperSizeResolver | None = None,
        preview_service: PreviewScanService | None = None,
        scan_client_factory: Callable[[str], EsclScanClient] | None = None,
        naming_engine: FileNamingEngine | None = None,
        exporter: ImageExporter | None = None,
        config_store: UserConfigStore | None = None,
        run_preview_async: bool = False,
    ) -> None:
        super().__init__(parent)
        self._discovery_service = discovery_service or EsclScannerDiscovery(timeout_seconds=1.2)
        self._capabilities_fetcher = capabilities_fetcher
        self._paper_size_resolver = paper_size_resolver or PaperSizeResolver()
        self._scan_client_factory = scan_client_factory or (lambda url: EsclScanClient(url))
        self._preview_service = preview_service or PreviewScanService(
            scan_client_factory=self._scan_client_factory
        )
        self._naming_engine = naming_engine or FileNamingEngine()
        self._exporter = exporter or ImageExporter()
        self._config_store = config_store
        self._run_preview_async = run_preview_async
        self._image_provider = PreviewImageProvider()

        self._previewFinishedInternal.connect(self._on_preview_finished)
        self._previewFailedInternal.connect(self._on_preview_failed)
        self._scanSaveFinishedInternal.connect(self._on_scan_save_finished)
        self._scanSaveFailedInternal.connect(self._on_scan_save_failed)

        loaded_cfg = self._config_store.load() if self._config_store is not None else UserConfig()

        self._discovered_by_label: dict[str, DiscoveredScanner] = {}
        self._current_capabilities: ScannerCapabilities | None = None
        self._source_caps_by_label: dict[str, InputSourceCaps] = {
            INPUT_SOURCE_LABELS[InputSourceKind.PLATEN]: DEFAULT_PLATEN_CAPS,
        }

        self._scanners: list[str] = ["スキャナ未検出（「スキャナを再検索」またはIP指定）"]
        self._selected_scanner: str = self._scanners[0]

        self._input_sources: list[str] = [INPUT_SOURCE_LABELS[InputSourceKind.PLATEN]]
        self._selected_input_source: str = self._input_sources[0]

        initial_paper_options = self._paper_size_resolver.resolve_for_source(DEFAULT_PLATEN_CAPS)
        self._paper_options_by_label: dict[str, PaperSizeOption] = {
            opt.display_label: opt for opt in initial_paper_options
        }
        self._paper_sizes: list[str] = [opt.display_label for opt in initial_paper_options]
        a4_default = next(
            (opt.display_label for opt in initial_paper_options if opt.code in ("A4", "A4_LANDSCAPE")),
            self._paper_sizes[0],
        )
        self._selected_paper_size: str = a4_default

        self._resolutions: list[int] = list(DEFAULT_PLATEN_CAPS.resolutions)
        self._selected_resolution: int = (
            loaded_cfg.resolution_dpi
            if loaded_cfg.resolution_dpi in self._resolutions
            else (300 if 300 in self._resolutions else self._resolutions[0])
        )

        self._save_formats: list[str] = ["PDF", "JPEG", "PNG"]
        self._selected_save_format: str = normalize_save_format(loaded_cfg.save_format)
        self._save_directory: str = loaded_cfg.save_directory
        self._naming_rule: str = loaded_cfg.naming_rule
        self._jpeg_quality: int = loaded_cfg.jpeg_quality
        self._multipage_pdf: bool = loaded_cfg.multipage_pdf

        self._status_message: str = "準備完了（Brother × macOS 優先モード）"
        self._is_busy: bool = False
        self._cancel_requested: bool = False

        self._preview_session: PreviewSession | None = None
        self._preview_image_url: str = ""
        self._preview_zoom: float = 1.0

        self._continuous_session: ContinuousScanSession | None = None
        self._saved_file_count: int = 0
        self._last_saved_file_name: str = ""
        self._pending_pdf_page_count: int = 0
        self._next_filename_preview: str = ""
        self._refresh_next_filename_preview()

    @property
    def image_provider(self) -> PreviewImageProvider:
        """Return the `PreviewImageProvider` bound to `image://preview`."""
        return self._image_provider

    @property
    def preview_session(self) -> PreviewSession | None:
        """Return the latest `PreviewSession` if a preview has been captured."""
        return self._preview_session

    def _persist_config(self) -> None:
        if self._config_store is None:
            return
        scanner = self._discovered_by_label.get(self._selected_scanner)
        cfg = UserConfig(
            save_directory=self._save_directory,
            naming_rule=self._naming_rule,
            save_format=self._selected_save_format,
            resolution_dpi=self._selected_resolution,
            jpeg_quality=self._jpeg_quality,
            multipage_pdf=self._multipage_pdf,
            last_scanner_url=scanner.base_url if scanner is not None else "",
        )
        try:
            self._config_store.save(cfg)
        except OSError:
            pass

    def _refresh_next_filename_preview(self) -> None:
        start_seq = self._continuous_session.next_seq if self._continuous_session is not None else 1
        try:
            preview = self._naming_engine.preview_next_filename(
                Path(self._save_directory).expanduser(),
                self._naming_rule,
                file_format=self._selected_save_format,
                start_seq=start_seq,
            )
        except Exception:
            preview = self._naming_engine.render_filename(
                self._naming_rule,
                seq=start_seq,
                file_format=self._selected_save_format,
            )
        if self._next_filename_preview != preview:
            self._next_filename_preview = preview
            self.nextFilenamePreviewChanged.emit()

    @Property("QStringList", notify=scannerChanged)
    def scanners(self) -> list[str]:
        return self._scanners

    @Property(str, notify=scannerChanged)
    def selectedScanner(self) -> str:
        return self._selected_scanner

    @selectedScanner.setter
    def selectedScanner(self, value: str) -> None:
        if self._selected_scanner != value:
            self._selected_scanner = value
            self.scannerChanged.emit()
            self._load_capabilities_for_selected_scanner()

    @Property("QStringList", notify=inputSourceChanged)
    def inputSources(self) -> list[str]:
        return self._input_sources

    @Property(str, notify=inputSourceChanged)
    def selectedInputSource(self) -> str:
        return self._selected_input_source

    @selectedInputSource.setter
    def selectedInputSource(self, value: str) -> None:
        if self._selected_input_source != value and value in self._source_caps_by_label:
            self._selected_input_source = value
            self.inputSourceChanged.emit()
            self._apply_source_capabilities(self._source_caps_by_label[value])

    @Property("QStringList", notify=paperSizeChanged)
    def paperSizes(self) -> list[str]:
        return self._paper_sizes

    @Property(str, notify=paperSizeChanged)
    def selectedPaperSize(self) -> str:
        return self._selected_paper_size

    @selectedPaperSize.setter
    def selectedPaperSize(self, value: str) -> None:
        if self._selected_paper_size != value:
            self._selected_paper_size = value
            self.paperSizeChanged.emit()

    @Property("QVariantMap", notify=paperSizeChanged)
    def currentPlacementGuide(self) -> dict[str, object]:
        """Return the placement guide dictionary for the currently selected paper size."""
        return self.getPlacementGuide(self._selected_paper_size)

    @Slot(str, result="QVariantMap")
    def getPlacementGuide(self, paper_label: str) -> dict[str, object]:
        """Return the placement guide dictionary for any paper size label (e.g., hovered in dropdown)."""
        active_caps = self._source_caps_by_label.get(self._selected_input_source, DEFAULT_PLATEN_CAPS)
        opt = self._paper_options_by_label.get(paper_label) or self.current_paper_size_option
        if opt is None:
            fallback_options = self._paper_size_resolver.resolve_for_source(active_caps)
            opt = fallback_options[0]
        guide = opt.build_placement_guide(active_caps.max_width, active_caps.max_height)
        return guide.to_dict()

    @Property("QVariantList", notify=resolutionChanged)
    def resolutions(self) -> list[int]:
        return self._resolutions

    @Property(int, notify=resolutionChanged)
    def selectedResolution(self) -> int:
        return self._selected_resolution

    @selectedResolution.setter
    def selectedResolution(self, value: int) -> None:
        int_val = int(value)
        if self._selected_resolution != int_val:
            self._selected_resolution = int_val
            self.resolutionChanged.emit()
            self._persist_config()

    @Property("QStringList", notify=saveFormatChanged)
    def saveFormats(self) -> list[str]:
        return self._save_formats

    @Property(str, notify=saveFormatChanged)
    def selectedSaveFormat(self) -> str:
        return self._selected_save_format

    @selectedSaveFormat.setter
    def selectedSaveFormat(self, value: str) -> None:
        normalized = normalize_save_format(value)
        if self._selected_save_format != normalized:
            self._selected_save_format = normalized
            self.saveFormatChanged.emit()
            self._refresh_next_filename_preview()
            self._persist_config()

    @Property(str, notify=saveDirectoryChanged)
    def saveDirectory(self) -> str:
        return self._save_directory

    @saveDirectory.setter
    def saveDirectory(self, value: str) -> None:
        cleaned = value.strip() or "~/Pictures/Scans"
        if self._save_directory != cleaned:
            self._save_directory = cleaned
            self.saveDirectoryChanged.emit()
            self._refresh_next_filename_preview()
            self._persist_config()

    @Slot(str)
    def setSaveDirectoryFromUrl(self, folder_url: str) -> None:
        """Convert a QML `FolderDialog` file URL to an OS-native path and update `saveDirectory`."""
        qurl = QUrl(folder_url)
        local_path = qurl.toLocalFile() if qurl.isLocalFile() else folder_url
        if local_path:
            self.saveDirectory = local_path

    @Property(str, notify=namingRuleChanged)
    def namingRule(self) -> str:
        return self._naming_rule

    @namingRule.setter
    def namingRule(self, value: str) -> None:
        if self._naming_rule != value:
            self._naming_rule = value
            self.namingRuleChanged.emit()
            self._refresh_next_filename_preview()
            self._persist_config()

    @Property(int, notify=jpegQualityChanged)
    def jpegQuality(self) -> int:
        return self._jpeg_quality

    @jpegQuality.setter
    def jpegQuality(self, value: int) -> None:
        clamped = max(1, min(100, int(value)))
        if self._jpeg_quality != clamped:
            self._jpeg_quality = clamped
            self.jpegQualityChanged.emit()
            self._persist_config()

    @Property(bool, notify=multipagePdfChanged)
    def multipagePdf(self) -> bool:
        return self._multipage_pdf

    @multipagePdf.setter
    def multipagePdf(self, value: bool) -> None:
        flag = bool(value)
        if self._multipage_pdf != flag:
            self._multipage_pdf = flag
            self.multipagePdfChanged.emit()
            self._persist_config()

    @Property(str, notify=nextFilenamePreviewChanged)
    def nextFilenamePreview(self) -> str:
        return self._next_filename_preview

    @Property(int, notify=scanSaveChanged)
    def savedFileCount(self) -> int:
        return self._saved_file_count

    @Property(str, notify=scanSaveChanged)
    def lastSavedFileName(self) -> str:
        return self._last_saved_file_name

    @Property(int, notify=scanSaveChanged)
    def pendingPdfPageCount(self) -> int:
        return self._pending_pdf_page_count

    @Property(str, notify=statusMessageChanged)
    def statusMessage(self) -> str:
        return self._status_message

    def set_status_message(self, message: str) -> None:
        if self._status_message != message:
            self._status_message = message
            self.statusMessageChanged.emit()

    @Property(bool, notify=isBusyChanged)
    def isBusy(self) -> bool:
        return self._is_busy

    def _set_busy(self, busy: bool) -> None:
        if self._is_busy != busy:
            self._is_busy = busy
            self.isBusyChanged.emit()

    @Property(bool, notify=previewChanged)
    def hasPreview(self) -> bool:
        return self._preview_session is not None and bool(self._preview_image_url)

    @Property(str, notify=previewChanged)
    def previewImageUrl(self) -> str:
        return self._preview_image_url

    @Property(int, notify=previewChanged)
    def previewDpi(self) -> int:
        if self._preview_session is not None:
            return self._preview_session.preview_dpi
        active_caps = self._source_caps_by_label.get(self._selected_input_source, DEFAULT_PLATEN_CAPS)
        return select_preview_dpi(active_caps.resolutions)

    @Property(float, notify=previewZoomChanged)
    def previewZoom(self) -> float:
        return self._preview_zoom

    @Slot()
    def zoomInPreview(self) -> None:
        self._preview_zoom = min(4.0, round(self._preview_zoom + 0.25, 2))
        self.previewZoomChanged.emit()

    @Slot()
    def zoomOutPreview(self) -> None:
        self._preview_zoom = max(0.5, round(self._preview_zoom - 0.25, 2))
        self.previewZoomChanged.emit()

    @Slot()
    def resetPreviewZoom(self) -> None:
        if self._preview_zoom != 1.0:
            self._preview_zoom = 1.0
            self.previewZoomChanged.emit()

    @property
    def current_paper_size_option(self) -> PaperSizeOption | None:
        """Return the currently selected `PaperSizeOption` domain object."""
        return self._paper_options_by_label.get(self._selected_paper_size)

    def _apply_capabilities(self, caps: ScannerCapabilities) -> None:
        """Update available input sources, paper sizes, and resolutions from `ScannerCapabilities`."""
        self._current_capabilities = caps
        source_map: dict[str, InputSourceCaps] = {}
        for src_caps in caps.available_sources:
            label = INPUT_SOURCE_LABELS.get(src_caps.source, str(src_caps.source))
            source_map[label] = src_caps

        if not source_map:
            source_map[INPUT_SOURCE_LABELS[InputSourceKind.PLATEN]] = DEFAULT_PLATEN_CAPS

        self._source_caps_by_label = source_map
        self._input_sources = list(source_map.keys())
        if self._selected_input_source not in self._source_caps_by_label:
            self._selected_input_source = self._input_sources[0]
        self.inputSourceChanged.emit()

        active_source_caps = self._source_caps_by_label[self._selected_input_source]
        self._apply_source_capabilities(active_source_caps)

    def _apply_source_capabilities(self, source_caps: InputSourceCaps) -> None:
        """Update paper sizes and DPI options for the active input source."""
        paper_options = self._paper_size_resolver.resolve_for_source(source_caps)
        self._paper_options_by_label = {opt.display_label: opt for opt in paper_options}
        new_paper_labels = [opt.display_label for opt in paper_options]

        prev_paper = self._selected_paper_size
        self._paper_sizes = new_paper_labels
        if prev_paper in self._paper_options_by_label:
            self._selected_paper_size = prev_paper
        else:
            a4_label = next(
                (opt.display_label for opt in paper_options if opt.code in ("A4", "A4_LANDSCAPE")),
                new_paper_labels[0],
            )
            self._selected_paper_size = a4_label
        self.paperSizeChanged.emit()

        new_resolutions = list(source_caps.resolutions) if source_caps.resolutions else [150, 300, 600]
        prev_dpi = self._selected_resolution
        self._resolutions = new_resolutions
        if prev_dpi in new_resolutions:
            self._selected_resolution = prev_dpi
        elif 300 in new_resolutions:
            self._selected_resolution = 300
        else:
            self._selected_resolution = new_resolutions[0]
        self.resolutionChanged.emit()
        self.previewChanged.emit()

    def _load_capabilities_for_selected_scanner(self) -> None:
        scanner = self._discovered_by_label.get(self._selected_scanner)
        if scanner is None:
            return
        try:
            caps = self._capabilities_fetcher(scanner.capabilities_url)
            self._apply_capabilities(caps)
            model_title = caps.make_and_model or scanner.name
            max_mm = f"{caps.primary_source.max_width_mm:.0f}×{caps.primary_source.max_height_mm:.0f}mm"
            self.set_status_message(
                f"接続完了: {model_title} (最大 {max_mm} / {len(self._paper_sizes)}種類のサイズ・向き / {len(self._resolutions)}段階のDPIに対応)"
            )
            self._persist_config()
        except Exception as exc:
            self.set_status_message(f"スキャナ能力の取得に失敗しました ({scanner.base_url}): {exc}")

    @Slot()
    def refreshScanners(self) -> None:
        """Discover eSCL scanners via mDNS and fetch capabilities of the selected scanner."""
        self.set_status_message("eSCL スキャナをネットワーク検索中...")
        try:
            found = self._discovery_service.discover()
        except Exception as exc:
            self.set_status_message(f"mDNS スキャナ検索エラー: {exc}")
            return

        if not found:
            self.set_status_message(
                "ネットワーク上に eSCL スキャナが見つかりませんでした（電源・同一Wi-Fi接続をご確認いただくか、IPアドレスを直接指定してください）"
            )
            return

        self._discovered_by_label = {scanner.display_label: scanner for scanner in found}
        self._scanners = list(self._discovered_by_label.keys())
        self._selected_scanner = self._scanners[0]
        self.scannerChanged.emit()
        self._load_capabilities_for_selected_scanner()

    @Slot(str)
    def addManualScanner(self, raw_address: str) -> None:
        """Add an eSCL scanner by IP address or URL and fetch its `ScannerCapabilities`."""
        try:
            scanner = _parse_manual_scanner_endpoint(raw_address)
        except ValueError as exc:
            self.set_status_message(str(exc))
            return

        try:
            caps = self._capabilities_fetcher(scanner.capabilities_url)
            if caps.make_and_model:
                scanner = DiscoveredScanner(
                    name=caps.make_and_model,
                    host=scanner.host,
                    port=scanner.port,
                    scheme=scanner.scheme,
                    resource_path=scanner.resource_path,
                    addresses=scanner.addresses,
                )
            label = scanner.display_label
            self._discovered_by_label[label] = scanner
            if label not in self._scanners or "未検出" in self._scanners[0]:
                self._scanners = list(self._discovered_by_label.keys())
            self._selected_scanner = label
            self.scannerChanged.emit()
            self._apply_capabilities(caps)
            max_mm = f"{caps.primary_source.max_width_mm:.0f}×{caps.primary_source.max_height_mm:.0f}mm"
            self.set_status_message(
                f"接続完了: {scanner.name} (最大 {max_mm} / {len(self._paper_sizes)}種類のサイズ・向き / {len(self._resolutions)}段階のDPIに対応)"
            )
            self._persist_config()
        except Exception as exc:
            self.set_status_message(f"指定アドレス ({scanner.capabilities_url}) からの能力取得に失敗しました: {exc}")

    def _on_preview_finished(self, session_obj: object) -> None:
        self._set_busy(False)
        if self._cancel_requested or not isinstance(session_obj, PreviewSession):
            self.set_status_message("プレビュースキャンをキャンセルしました")
            return
        try:
            url = self._image_provider.update_image_bytes(session_obj.image_bytes)
            self._preview_session = session_obj
            self._preview_image_url = url
            self._preview_zoom = 1.0
            self.previewZoomChanged.emit()
            self.previewChanged.emit()
            self.set_status_message(
                f"プレビュー完了 ({session_obj.preview_dpi} dpi / {session_obj.image_width_px}×{session_obj.image_height_px} px)"
            )
        except Exception as exc:
            self.set_status_message(f"プレビュー画像の読み込みに失敗しました: {exc}")

    def _on_preview_failed(self, error_msg: str) -> None:
        self._set_busy(False)
        if self._cancel_requested:
            self.set_status_message("プレビュースキャンをキャンセルしました")
        else:
            self.set_status_message(f"プレビュースキャンエラー: {error_msg}")

    def _ensure_scanner_connected(self) -> DiscoveredScanner | None:
        scanner = self._discovered_by_label.get(self._selected_scanner)
        if scanner is None:
            self.refreshScanners()
            scanner = self._discovered_by_label.get(self._selected_scanner)
        return scanner

    @Slot()
    def requestPreview(self) -> None:
        """Execute a fast low-DPI preview scan and display it on the QML preview canvas."""
        if self._is_busy:
            return

        scanner = self._ensure_scanner_connected()
        if scanner is None:
            self.set_status_message(
                "プレビューを実行するスキャナが接続されていません（「スキャナを再検索」またはIP接続を行ってください）"
            )
            return

        active_caps = self._source_caps_by_label.get(self._selected_input_source, DEFAULT_PLATEN_CAPS)
        paper_opt = self.current_paper_size_option
        if paper_opt is None:
            paper_opt = self._paper_size_resolver.resolve_for_source(active_caps)[0]

        preview_dpi = select_preview_dpi(active_caps.resolutions)
        self._cancel_requested = False
        self._set_busy(True)
        self.set_status_message(
            f"低解像度プレビュースキャン中 ({preview_dpi} dpi / {paper_opt.name} {paper_opt.orientation_label})..."
        )

        base_url = scanner.base_url

        def _worker() -> None:
            try:
                session = self._preview_service.execute_preview(base_url, active_caps, paper_opt)
                self._previewFinishedInternal.emit(session)
            except Exception as exc:
                self._previewFailedInternal.emit(str(exc))

        if self._run_preview_async:
            thread = threading.Thread(target=_worker, daemon=True)
            thread.start()
        else:
            _worker()

    @Slot()
    def cancelPreview(self) -> None:
        """Cancel an in-progress preview or main scan."""
        if self._is_busy:
            self._cancel_requested = True
            self._set_busy(False)
            self.set_status_message("スキャンをキャンセルしました")

    def _build_current_save_profile(self, scanner: DiscoveredScanner) -> ScanSaveProfile:
        active_caps = self._source_caps_by_label.get(self._selected_input_source, DEFAULT_PLATEN_CAPS)
        paper_opt = self.current_paper_size_option
        if paper_opt is None:
            paper_opt = self._paper_size_resolver.resolve_for_source(active_caps)[0]
        return ScanSaveProfile(
            base_url=scanner.base_url,
            source_caps=active_caps,
            paper_size=paper_opt,
            save_dir=Path(self._save_directory).expanduser(),
            naming_template=self._naming_rule,
            file_format=self._selected_save_format,
            resolution_dpi=self._selected_resolution,
            quality=self._jpeg_quality,
            multipage_pdf=self._multipage_pdf,
        )

    def _on_scan_save_finished(self, result_obj: object) -> None:
        self._set_busy(False)
        if self._cancel_requested or not isinstance(result_obj, ContinuousScanStepResult):
            self.set_status_message("スキャンをキャンセルしました")
            return

        # Show the newly scanned image on the preview canvas as immediate visual confirmation
        try:
            url = self._image_provider.update_image_bytes(result_obj.last_image_bytes)
            self._preview_image_url = url
            if self._preview_session is None and self._continuous_session is not None:
                prof = self._continuous_session.profile
                self._preview_session = PreviewSession(
                    image_bytes=result_obj.last_image_bytes,
                    content_type="image/jpeg",
                    image_width_px=self._image_provider.current_image.width(),
                    image_height_px=self._image_provider.current_image.height(),
                    preview_dpi=prof.resolution_dpi,
                    paper_size=prof.paper_size,
                    input_source=prof.source_caps.source,
                )
            self.previewChanged.emit()
        except Exception:
            pass

        self._saved_file_count = result_obj.total_saved_files
        self._pending_pdf_page_count = result_obj.buffered_page_count
        if result_obj.saved_results:
            last_path = result_obj.saved_results[-1].output_path
            self._last_saved_file_name = last_path.name
            self.set_status_message(
                f"保存完了 ({self._saved_file_count}件目): {last_path} — 次の原稿を置いて続けてスキャンできます"
            )
        else:
            self.set_status_message(
                f"マルチページPDFに {self._pending_pdf_page_count} ページ目を追加しました（続けてスキャン、または「PDFを保存して完了」）"
            )
        self.scanSaveChanged.emit()
        self._refresh_next_filename_preview()

    def _on_scan_save_failed(self, error_msg: str) -> None:
        self._set_busy(False)
        if self._cancel_requested:
            self.set_status_message("スキャンをキャンセルしました")
        else:
            self.set_status_message(f"本スキャン・保存エラー: {error_msg}")

    @Slot()
    def requestScanAndSave(self) -> None:
        """Execute main high-DPI scan and save using the locked-in continuous scan settings."""
        if self._is_busy:
            return

        scanner = self._ensure_scanner_connected()
        if scanner is None:
            self.set_status_message(
                "スキャンを実行するスキャナが接続されていません（「スキャナを再検索」またはIP接続を行ってください）"
            )
            return

        profile = self._build_current_save_profile(scanner)
        if self._continuous_session is None:
            self._continuous_session = ContinuousScanSession(
                profile,
                scan_client_factory=self._scan_client_factory,
                naming_engine=self._naming_engine,
                exporter=self._exporter,
            )
        else:
            self._continuous_session.update_profile(profile)

        self._cancel_requested = False
        self._set_busy(True)
        self.set_status_message(
            f"本スキャン中 ({profile.resolution_dpi} dpi / {profile.file_format} / 品質 {profile.quality}%)..."
        )
        session = self._continuous_session

        def _worker() -> None:
            try:
                step_result = session.scan_and_save_next()
                self._scanSaveFinishedInternal.emit(step_result)
            except Exception as exc:
                self._scanSaveFailedInternal.emit(str(exc))

        if self._run_preview_async:
            thread = threading.Thread(target=_worker, daemon=True)
            thread.start()
        else:
            _worker()

    @Slot()
    def finalizeMultipagePdf(self) -> None:
        """Write buffered pages in the active continuous session into one multi-page PDF."""
        if self._continuous_session is None or self._continuous_session.pending_page_count == 0:
            self.set_status_message("結合するスキャン済みページがありません")
            return
        try:
            exported = self._continuous_session.finalize_multipage_pdf()
            self._saved_file_count = len(self._continuous_session.saved_results)
            self._pending_pdf_page_count = 0
            self._last_saved_file_name = exported.output_path.name
            self.scanSaveChanged.emit()
            self._refresh_next_filename_preview()
            self.set_status_message(
                f"マルチページPDF保存完了 ({exported.page_count}ページ): {exported.output_path}"
            )
        except Exception as exc:
            self.set_status_message(f"マルチページPDF保存エラー: {exc}")
