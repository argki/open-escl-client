"""ViewModel bridging Python core logic and declarative QML UI."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlparse

from PySide6.QtCore import Property, QObject, Signal, Slot

from open_escl_client.core.paper_size import PaperSizeOption, PaperSizeResolver
from open_escl_client.escl.capabilities import (
    InputSourceCaps,
    InputSourceKind,
    ScannerCapabilities,
    fetch_scanner_capabilities,
)
from open_escl_client.escl.discovery import DiscoveredScanner, EsclScannerDiscovery

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

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        discovery_service: EsclScannerDiscovery | None = None,
        capabilities_fetcher: Callable[[str], ScannerCapabilities] = fetch_scanner_capabilities,
        paper_size_resolver: PaperSizeResolver | None = None,
    ) -> None:
        super().__init__(parent)
        self._discovery_service = discovery_service or EsclScannerDiscovery(timeout_seconds=1.2)
        self._capabilities_fetcher = capabilities_fetcher
        self._paper_size_resolver = paper_size_resolver or PaperSizeResolver()

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
        self._selected_paper_size: str = self._paper_sizes[0]

        self._resolutions: list[int] = list(DEFAULT_PLATEN_CAPS.resolutions)
        self._selected_resolution: int = 300 if 300 in self._resolutions else self._resolutions[0]

        self._save_formats: list[str] = ["PDF", "JPEG", "PNG"]
        self._selected_save_format: str = "PDF"
        self._save_directory: str = "~/Pictures/Scans"
        self._naming_rule: str = "Scan_{date}_{seq:03d}"
        self._status_message: str = "準備完了（Brother × macOS 優先モード）"
        self._is_busy: bool = False

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
            # Prefer A4 by default if supported, otherwise first option
            a4_label = next((opt.display_label for opt in paper_options if opt.code == "A4"), new_paper_labels[0])
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
                f"接続完了: {model_title} (最大 {max_mm} / {len(self._paper_sizes)}種類のサイズ・{len(self._resolutions)}段階のDPIに対応)"
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
                f"接続完了: {scanner.name} (最大 {max_mm} / {len(self._paper_sizes)}種類のサイズ・{len(self._resolutions)}段階のDPIに対応)"
            )
        except Exception as exc:
            self.set_status_message(f"指定アドレス ({scanner.capabilities_url}) からの能力取得に失敗しました: {exc}")

    @Slot()
    def requestPreview(self) -> None:
        """Trigger fast low-DPI preview scan (implemented in REQ-003)."""
        self.set_status_message("低解像度プレビュースキャンを実行準備中...")

    @Slot()
    def requestScanAndSave(self) -> None:
        """Trigger main scan and save (implemented in REQ-004)."""
        self.set_status_message(
            f"本スキャン開始: {self._selected_input_source} / {self._selected_paper_size} / {self._selected_resolution} dpi / {self._selected_save_format}"
        )
