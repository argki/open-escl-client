import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// ============================================================================
// PreviewArea.qml: 中央のプレビュー画像・自動トリミング確認キャンバス領域
// ----------------------------------------------------------------------------
// 役割:
//   - 低解像度で高速スキャンしたプレビュー画像の表示
//   - 色合い調整・裏透け除去のリアルタイムプレビュー確認
//   - 自動検出された複数枚のトリミング枠・角度補正イメージの表示と微調整
// ============================================================================
Rectangle {
    id: previewRoot
    objectName: "previewArea"
    color: "#E5E7EB"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 12

        // プレビューヘッダー（現在の設定サマリー表示）
        RowLayout {
            Layout.fillWidth: true

            Label {
                text: "プレビューキャンバス（色合い・自動トリミング確認）"
                font.pixelSize: 15
                font.bold: true
                color: "#1F2937"
            }

            Item { Layout.fillWidth: true }

            Label {
                objectName: "previewSummaryLabel"
                text: viewModel
                      ? (viewModel.selectedPaperSize + " / 本保存: " + viewModel.selectedResolution + " dpi")
                      : ""
                color: "#4B5563"
            }
        }

        // プレビュー原稿台ステージ
        Rectangle {
            id: canvasStage
            objectName: "canvasStage"
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "#FFFFFF"
            border.color: "#9CA3AF"
            border.width: 1
            radius: 6

            ColumnLayout {
                anchors.centerIn: parent
                spacing: 8

                Label {
                    Layout.alignment: Qt.AlignHCenter
                    text: "プレビュー画像がここに表示されます"
                    font.pixelSize: 16
                    font.bold: true
                    color: "#6B7280"
                }

                Label {
                    Layout.alignment: Qt.AlignHCenter
                    text: "下部の「高速プレビュー」を押すと、低解像度で素早く読み取り結果を確認できます"
                    font.pixelSize: 13
                    color: "#9CA3AF"
                }
            }
        }
    }
}
