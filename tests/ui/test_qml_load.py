"""Tests for declarative QML loading and AppViewModel bindings."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication

from open_escl_client.ui.app import MAIN_QML_PATH, create_qml_engine
from open_escl_client.ui.viewmodel import AppViewModel


def _ensure_qapp() -> QGuiApplication:
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app  # type: ignore[return-value]


def test_main_qml_file_exists() -> None:
    assert MAIN_QML_PATH.is_file()


def test_qml_engine_loads_main_window_and_components() -> None:
    _ensure_qapp()
    engine, vm = create_qml_engine()
    roots = engine.rootObjects()
    assert len(roots) == 1

    root = roots[0]
    assert root.property("title") == "Open eSCL Client"

    # Verify key declarative UI regions exist by objectName
    assert root.findChild(QObject, "scannerCombo") is not None
    assert root.findChild(QObject, "paperSizeCombo") is not None
    assert root.findChild(QObject, "resolutionCombo") is not None
    assert root.findChild(QObject, "saveDirField") is not None
    assert root.findChild(QObject, "namingRuleField") is not None
    assert root.findChild(QObject, "saveFormatCombo") is not None
    assert root.findChild(QObject, "previewArea") is not None
    assert root.findChild(QObject, "actionToolbar") is not None
    assert root.findChild(QObject, "previewButton") is not None
    assert root.findChild(QObject, "scanAndSaveButton") is not None

    # Clean up QML root objects
    for obj in roots:
        obj.deleteLater()
    engine.deleteLater()
    assert vm is not None


def test_viewmodel_properties_and_slots() -> None:
    vm = AppViewModel()
    assert "A4 (210 × 297 mm)" in vm.paperSizes
    assert vm.selectedResolution == 300
    assert vm.selectedSaveFormat == "PDF"

    vm.selectedPaperSize = "B5 (182 × 257 mm)"
    assert vm.selectedPaperSize == "B5 (182 × 257 mm)"

    vm.selectedResolution = 600
    assert vm.selectedResolution == 600

    vm.saveDirectory = "/tmp/scans"
    assert vm.saveDirectory == "/tmp/scans"

    vm.namingRule = "Receipt_{date}_{seq:04d}"
    assert vm.namingRule == "Receipt_{date}_{seq:04d}"

    vm.refreshScanners()
    assert "検索中" in vm.statusMessage

    vm.requestPreview()
    assert "プレビュー" in vm.statusMessage

    vm.requestScanAndSave()
    assert "600 dpi" in vm.statusMessage
