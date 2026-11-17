"""ViewModel bridging Python core logic and declarative QML UI."""

from __future__ import annotations

import threading
from collections.abc import Callable
from urllib.parse import urlparse

from PySide6.QtCore import Property, QObject, Signal, Slot

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
    statusMessageChanged = Signal()
    isBusyChanged = Signal()
    previewChanged = Signal()
    previewZoomChanged = Signal()

    # Internal thread-safe completion signals for background preview scan
    _previewFinishedInternal = Signal(object)
    _previewFailedInternal = Signal(str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        discovery_service: EsclScannerDiscovery | None = None,
        capabilities_fetcher: Callable[[str], ScannerCapabilities] = fetch_scanner_capabilities,
        paper_size_resolver: PaperSizeResolver | None = None,
        preview_service: PreviewScanService | None = None,
        run_preview_async: bool = False,
    ) -> None:
        super().__init__(parent)
        self._discovery_service = discovery_service or EsclScannerDiscovery(timeout_seconds=1.2)
        self._capabilities_fetcher = capabilities_fetcher
        self._paper_size_resolver = paper_size_resolver or PaperSizeResolver()
        self._preview_service = preview_service or PreviewScanService()
        self._run_preview_async = run_preview_async
        self._image_provider = PreviewImageProvider()

        self._previewFinishedInternal.connect(self._on_preview_finished)
        self._previewFailedInternal.connect(self._on_preview_failed)

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
        self._selected_resolution: int = 300 if 300 in self._resolutions else self._resolutions[0]

        self._save_formats: list[str] = ["PDF", "JPEG", "PNG"]
        self._selected_save_format: str = "PDF"
        self._save_directory: str = "~/Pictures/Scans"
        self._naming_rule: str = "Scan_{date}_{seq:03d}"
        self._status_message: str = "準備完了（Brother × macOS 優先モード）"
        self._is_busy: bool = False
        self._cancel_requested: bool = False

        self._preview_session: PreviewSession | None = None
        self._preview_image_url: str = ""
        self._preview_zoom: float = 1.0

    @property
    def image_provider(self) -> PreviewImageProvider:
        """Return the `PreviewImageProvider` bound to `image://preview`."""
        return self._image_provider

    @property
    def preview_session(self) -> PreviewSession | None:
        """Return the latest `PreviewSession` if a preview has been captured."""
        return self._preview_session

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
        if self._selected_resolution != int(value):
            self._selected_resolution = int(value)
            self.resolutionChanged.emit()

    @Property("QStringList", notify=saveFormatChanged)
    def saveFormats(self) -> list[str]:
        return self._save_formats

    @Property(str, notify=saveFormatChanged)
    def selectedSaveFormat(self) -> str:
        return self._selected_save_format

    @selectedSaveFormat.setter
    def selectedSaveFormat(self, value: str) -> None:
        if self._selected_save_format != value:
            self._selected_save_format = value
            self.saveFormatChanged.emit()

    @Property(str, notify=saveDirectoryChanged)
    def saveDirectory(self) -> str:
        return self._save_directory

    @saveDirectory.setter
    def saveDirectory(self, value: str) -> None:
        if self._save_directory != value:
            self._save_directory = value
            self.saveDirectoryChanged.emit()

    @Property(str, notify=namingRuleChanged)
    def namingRule(self) -> str:
        return self._naming_rule

    @namingRule.setter
    def namingRule(self, value: str) -> None:
        if self._naming_rule != value:
            self._naming_rule = value
            self.namingRuleChanged.emit()

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

    @Slot()
    def requestPreview(self) -> None:
        """Execute a fast low-DPI preview scan and display it on the QML preview canvas."""
        if self._is_busy:
            return

        scanner = self._discovered_by_label.get(self._selected_scanner)
        if scanner is None:
            self.refreshScanners()
            scanner = self._discovered_by_label.get(self._selected_scanner)
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
        """Cancel an in-progress preview scan."""
        if self._is_busy:
            self._cancel_requested = True
            self._set_busy(False)
            self.set_status_message("プレビュースキャンをキャンセルしました")

    @Slot()
    def requestScanAndSave(self) -> None:
        """Trigger main scan and save (implemented in REQ-004)."""
        self.set_status_message(
            f"本スキャン開始: {self._selected_input_source} / {self._selected_paper_size} / {self._selected_resolution} dpi / {self._selected_save_format}"
        )
