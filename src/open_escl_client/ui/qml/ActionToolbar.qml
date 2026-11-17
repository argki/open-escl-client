import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// ============================================================================
// ActionToolbar.qml: 下部のスキャン操作・連続保存アクションバー
// ----------------------------------------------------------------------------
// 配置要素:
//   - 左側: 現在のステータスメッセージ表示 + 保存済みファイル数/最新ファイル表示
//   - 右側: キャンセル、マルチページPDF確定、「高速プレビュー」、「スキャンして保存」ボタン
// ============================================================================
Rectangle {
    id: toolbarRoot
    objectName: "actionToolbar"
    color: "#FFFFFF"

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 20
        anchors.rightMargin: 20
        spacing: 10

        // ステータスメッセージ & 保存済みカウンタ表示領域
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2

            Label {
                id: statusLabel
                objectName: "statusLabel"
                text: viewModel ? viewModel.statusMessage : ""
                color: "#374151"
                font.pixelSize: 13
                elide: Text.ElideRight
                Layout.fillWidth: true
            }

            Label {
                id: savedCountLabel
                objectName: "savedCountLabel"
                visible: viewModel ? (viewModel.savedFileCount > 0 || viewModel.pendingPdfPageCount > 0) : false
                text: {
                    if (!viewModel) return ""
                    var parts = []
                    if (viewModel.savedFileCount > 0) {
                        parts.push("保存済み: " + viewModel.savedFileCount + "件 (最新: " + viewModel.lastSavedFileName + ")")
                    }
                    if (viewModel.pendingPdfPageCount > 0) {
                        parts.push("PDF結合待ち: " + viewModel.pendingPdfPageCount + "ページ")
                    }
                    return parts.join(" / ")
                }
                color: "#047857"
                font.pixelSize: 11
                font.bold: true
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
        }

        // スキャン実行中のキャンセルボタン
        Button {
            id: cancelScanButton
            objectName: "cancelScanButton"
            text: "キャンセル"
            visible: viewModel ? viewModel.isBusy : false
            Layout.preferredHeight: 40
            Layout.preferredWidth: 110
            onClicked: {
                if (viewModel) {
                    viewModel.cancelPreview()
                }
            }
        }

        // マルチページPDF結合・保存確定ボタン
        Button {
            id: finalizePdfButton
            objectName: "finalizePdfButton"
            visible: viewModel ? viewModel.pendingPdfPageCount > 0 : false
            enabled: viewModel ? !viewModel.isBusy : false
            text: viewModel ? ("PDFを保存 (" + viewModel.pendingPdfPageCount + "P)") : "PDFを保存"
            Layout.preferredHeight: 40
            Layout.preferredWidth: 160
            onClicked: {
                if (viewModel) {
                    viewModel.finalizeMultipagePdf()
                }
            }
        }

        // 高速プレビュー実行ボタン
        Button {
            id: previewButton
            objectName: "previewButton"
            text: viewModel
                  ? ("高速プレビュー (" + viewModel.previewDpi + " dpi)")
                  : "高速プレビュー (低解像度)"
            enabled: viewModel ? !viewModel.isBusy : true
            Layout.preferredHeight: 40
            Layout.preferredWidth: 205
            onClicked: {
                if (viewModel) {
                    viewModel.requestPreview()
                }
            }
        }

        // 本スキャン＆保存（連続スキャン対応）ボタン
        Button {
            id: scanAndSaveButton
            objectName: "scanAndSaveButton"
            text: (viewModel && viewModel.multipagePdf && viewModel.selectedSaveFormat === "PDF")
                  ? "ページを追加スキャン"
                  : "スキャンして保存 (連続可)"
            highlighted: true
            enabled: viewModel ? !viewModel.isBusy : true
            Layout.preferredHeight: 40
            Layout.preferredWidth: 230
            onClicked: {
                if (viewModel) {
                    viewModel.requestScanAndSave()
                }
            }
        }
    }
}
