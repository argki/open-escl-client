import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// ============================================================================
// PreviewArea.qml: 中央のプレビュー画像・自動トリミング確認キャンバス領域
// ----------------------------------------------------------------------------
// 役割:
//   - 低解像度で高速スキャンしたプレビュー画像の表示（ズーム・スクロール対応）
//   - プレビュー使用DPIおよび本保存用DPIの比較サマリー表示
//   - スキャン実行中のローディングインジケータ表示
// ============================================================================
Rectangle {
    id: previewRoot
    objectName: "previewArea"
    color: "#E5E7EB"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 12

        // --------------------------------------------------------------------
        // 1. プレビューヘッダー（設定サマリー・プレビューDPI・ズーム操作ボタン）
        // --------------------------------------------------------------------
        RowLayout {
            Layout.fillWidth: true
            spacing: 10

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
                      ? (viewModel.selectedPaperSize + " / プレビュー: " + viewModel.previewDpi + " dpi / 本保存: " + viewModel.selectedResolution + " dpi")
                      : ""
                font.pixelSize: 12
                color: "#4B5563"
            }

            // ズーム操作コントロール
            RowLayout {
                spacing: 4
                visible: viewModel ? viewModel.hasPreview : false

                Button {
                    objectName: "zoomOutButton"
                    text: "－"
                    implicitWidth: 32
                    implicitHeight: 28
                    onClicked: if (viewModel) viewModel.zoomOutPreview()
                }

                Button {
                    objectName: "zoomResetButton"
                    text: viewModel ? Math.round(viewModel.previewZoom * 100) + "%" : "100%"
                    implicitWidth: 58
                    implicitHeight: 28
                    onClicked: if (viewModel) viewModel.resetPreviewZoom()
                }

                Button {
                    objectName: "zoomInButton"
                    text: "＋"
                    implicitWidth: 32
                    implicitHeight: 28
                    onClicked: if (viewModel) viewModel.zoomInPreview()
                }
            }
        }

        // --------------------------------------------------------------------
        // 2. プレビュー原稿台ステージ（画像表示・ズーム・ローディングオーバーレイ）
        // --------------------------------------------------------------------
        Rectangle {
            id: canvasStage
            objectName: "canvasStage"
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "#FFFFFF"
            border.color: "#9CA3AF"
            border.width: 1
            radius: 6
            clip: true

            // 未スキャン時の案内プレースホルダ
            ColumnLayout {
                anchors.centerIn: parent
                spacing: 8
                visible: viewModel ? (!viewModel.hasPreview && !viewModel.isBusy) : true

                Label {
                    Layout.alignment: Qt.AlignHCenter
                    text: "プレビュー画像がここに表示されます"
                    font.pixelSize: 16
                    font.bold: true
                    color: "#6B7280"
                }

                Label {
                    Layout.alignment: Qt.AlignHCenter
                    text: "下部の「高速プレビュー」を押すと、低解像度（" + (viewModel ? viewModel.previewDpi : 100) + " dpi）で素早く読み取り結果を確認できます"
                    font.pixelSize: 13
                    color: "#9CA3AF"
                }
            }

            // プレビュー画像表示領域（ズーム・スクロール対応）
            Flickable {
                id: previewFlickable
                objectName: "previewFlickable"
                anchors.fill: parent
                anchors.margins: 12
                clip: true
                visible: viewModel ? viewModel.hasPreview : false
                contentWidth: Math.max(width, previewCanvasImage.width)
                contentHeight: Math.max(height, previewCanvasImage.height)

                Image {
                    id: previewCanvasImage
                    objectName: "previewCanvasImage"
                    anchors.centerIn: parent
                    width: previewFlickable.width * (viewModel ? viewModel.previewZoom : 1.0)
                    height: previewFlickable.height * (viewModel ? viewModel.previewZoom : 1.0)
                    fillMode: Image.PreserveAspectFit
                    cache: false
                    asynchronous: false
                    source: viewModel ? viewModel.previewImageUrl : ""
                }
            }

            // スキャン実行中のローディングオーバーレイ
            Rectangle {
                id: busyOverlay
                objectName: "busyOverlay"
                anchors.fill: parent
                color: "#CCFFFFFF"
                visible: viewModel ? viewModel.isBusy : false

                ColumnLayout {
                    anchors.centerIn: parent
                    spacing: 12

                    BusyIndicator {
                        Layout.alignment: Qt.AlignHCenter
                        running: viewModel ? viewModel.isBusy : false
                    }

                    Label {
                        Layout.alignment: Qt.AlignHCenter
                        text: viewModel ? viewModel.statusMessage : "プレビュースキャン中..."
                        font.pixelSize: 14
                        font.bold: true
                        color: "#1F2937"
                    }
                }
            }
        }
    }
}
