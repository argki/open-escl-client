"""QML application loader and window setup."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from open_escl_client.ui.viewmodel import AppViewModel

QML_DIR = Path(__file__).resolve().parent / "qml"
MAIN_QML_PATH = QML_DIR / "Main.qml"


def create_qml_engine(view_model: AppViewModel | None = None) -> tuple[QQmlApplicationEngine, AppViewModel]:
    """Create and initialize the QQmlApplicationEngine with `Main.qml`."""
    vm = view_model if view_model is not None else AppViewModel()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("viewModel", vm)
    engine.addImportPath(str(QML_DIR))
    engine.load(QUrl.fromLocalFile(str(MAIN_QML_PATH)))
    return engine, vm


def run_gui_app(argv: list[str] | None = None) -> int:
    """Launch the Qt Quick / QML desktop application."""
    QQuickStyle.setStyle("Basic")
    app = QGuiApplication(argv or [])
    app.setApplicationName("Open eSCL Client")
    app.setOrganizationName("open-escl-client")

    engine, vm = create_qml_engine()
    if not engine.rootObjects():
        return 1
    QTimer.singleShot(150, vm.refreshScanners)
    return app.exec()
