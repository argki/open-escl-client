import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// ============================================================================
// ActionToolbar.qml: 下部のスキャン操作・連続保存アクションバー
// ----------------------------------------------------------------------------
// 配置要素:
//   - 左側: 現在のステータスメッセージ表示
//   - 右側: 「高速プレビュー（低DPI）」ボタン、「スキャンして保存（連続スキャン対応）」ボタン
// ============================================================================
Rectangle {
    id: toolbarRoot
    objectName: "actionToolbar"
    color: "#FFFFFF"

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 20
        anchors.rightMargin: 20
        spacing: 12

        // ステータスメッセージ表示領域
        Label {
            id: statusLabel
            objectName: "statusLabel"
            text: viewModel ? viewModel.statusMessage : ""
            color: "#374151"
            font.pixelSize: 13
            elide: Text.ElideRight
            Layout.fillWidth: true
        }

        // 高速プレビュー実行ボタン
        Button {
            id: previewButton
            objectName: "previewButton"
            text: "高速プレビュー (低解像度)"
            Layout.preferredHeight: 40
            Layout.preferredWidth: 210
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
            text: "スキャンして保存 (連続スキャン可)"
            highlighted: true
            Layout.preferredHeight: 40
            Layout.preferredWidth: 250
            onClicked: {
                if (viewModel) {
                    viewModel.requestScanAndSave()
                }
            }
        }
    }
}
