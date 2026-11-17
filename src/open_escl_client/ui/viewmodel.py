"""ViewModel bridging Python core logic and declarative QML UI."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot


class AppViewModel(QObject):
    """Main ViewModel exposed to QML as `viewModel`."""

    scannerChanged = Signal()
    paperSizeChanged = Signal()
    resolutionChanged = Signal()
    saveFormatChanged = Signal()
    saveDirectoryChanged = Signal()
    namingRuleChanged = Signal()
    statusMessageChanged = Signal()
    isBusyChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._scanners: list[str] = ["スキャナ未検出（検索してください）"]
        self._selected_scanner: str = self._scanners[0]
        self._paper_sizes: list[str] = ["A4 (210 × 297 mm)", "B5 (182 × 257 mm)", "最大サイズ（自動トリミング用）"]
        self._selected_paper_size: str = self._paper_sizes[0]
        self._resolutions: list[int] = [150, 300, 600]
        self._selected_resolution: int = 300
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
        if self._selected_resolution != value:
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

    @Slot()
    def refreshScanners(self) -> None:
        """Trigger scanner discovery (implemented in REQ-002)."""
        self.set_status_message("eSCL スキャナを検索中...")

    @Slot()
    def requestPreview(self) -> None:
        """Trigger fast low-DPI preview scan (implemented in REQ-003)."""
        self.set_status_message("低解像度プレビュースキャンを実行準備中...")

    @Slot()
    def requestScanAndSave(self) -> None:
        """Trigger main scan and save (implemented in REQ-004)."""
        self.set_status_message(
            f"本スキャン開始: {self._selected_paper_size} / {self._selected_resolution} dpi / {self._selected_save_format}"
        )
