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
                    text: "下部の「高速プレビュー」を押すと、低解像度（" + (viewModel ? viewModel.selectedPreviewDpi : 100) + " dpi）で素早く読み取り結果を確認できます"
                    font.pixelSize: 13
                    color: "#9CA3AF"
                }
            }

            // プレビュー画像表示領域（ズーム・スクロール・トリミング枠オーバーレイ対応）
            Flickable {
                id: previewFlickable
                objectName: "previewFlickable"
                anchors.fill: parent
                anchors.margins: 12
                clip: true
                interactive: !cropOverlay.dragging
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

                    // 実際の画像描画領域 (paintedWidth × paintedHeight) にぴったり重ねるオーバーレイ
                    CropOverlay {
                        id: cropOverlay
                        x: (previewCanvasImage.width - previewCanvasImage.paintedWidth) * 0.5
                        y: (previewCanvasImage.height - previewCanvasImage.paintedHeight) * 0.5
                        width: previewCanvasImage.paintedWidth
                        height: previewCanvasImage.paintedHeight
                    }
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

        // --------------------------------------------------------------------
        // 3. 切り出し・角度補正・自動回転結果のサムネイル確認ストリップ
        // --------------------------------------------------------------------
        Rectangle {
            id: cropThumbnailsStrip
            objectName: "cropThumbnailsStrip"
            Layout.fillWidth: true
            Layout.preferredHeight: 158
            color: "#F9FAFB"
            border.color: "#D1D5DB"
            border.width: 1
            radius: 6
            visible: viewModel ? (viewModel.hasPreview && viewModel.autoCropEnabled && viewModel.cropCount > 0) : false

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 8
                spacing: 6

                RowLayout {
                    Layout.fillWidth: true

                    Label {
                        text: "切り出し・角度補正・自動回転プレビュー (" + (viewModel ? viewModel.cropCount : 0) + "件)"
                        font.pixelSize: 12
                        font.bold: true
                        color: "#374151"
                    }

                    Item { Layout.fillWidth: true }

                    Label {
                        text: "各カードをクリックして選択・90°回転や余白を微調整できます"
                        font.pixelSize: 11
                        color: "#6B7280"
                    }
                }

                ListView {
                    id: cropThumbnailList
                    objectName: "cropThumbnailList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    orientation: ListView.Horizontal
                    spacing: 10
                    clip: true
                    model: viewModel ? viewModel.cropBoxes : []

                    delegate: Rectangle {
                        id: thumbCard
                        width: 128
                        height: cropThumbnailList.height
                        radius: 5
                        color: modelData.selected ? "#EFF6FF" : "#FFFFFF"
                        border.color: modelData.selected ? "#2563EB" : "#D1D5DB"
                        border.width: modelData.selected ? 2 : 1

                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (viewModel) {
                                    viewModel.selectCropBox(modelData.listIndex)
                                }
                            }
                        }

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 5
                            spacing: 3

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 4

                                Label {
                                    text: "#" + modelData.index
                                    font.pixelSize: 11
                                    font.bold: true
                                    color: modelData.selected ? "#1D4ED8" : "#059669"
                                }

                                Item { Layout.fillWidth: true }

                                Label {
                                    text: modelData.effectiveRotationDeg + "°"
                                    font.pixelSize: 10
                                    font.bold: true
                                    color: "#4B5563"
                                }
                            }

                            Image {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                fillMode: Image.PreserveAspectFit
                                cache: false
                                asynchronous: false
                                source: modelData.thumbnailUrl || ""
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 4

                                Button {
                                    text: "↺"
                                    implicitWidth: 26
                                    implicitHeight: 20
                                    padding: 0
                                    onClicked: {
                                        if (viewModel) {
                                            viewModel.rotateCropBox(modelData.listIndex, -90)
                                        }
                                    }
                                }

                                Label {
                                    Layout.fillWidth: true
                                    horizontalAlignment: Text.AlignHCenter
                                    text: (modelData.effectiveAngleDeg >= 0 ? "+" : "") + modelData.effectiveAngleDeg.toFixed(1) + "°"
                                    font.pixelSize: 10
                                    color: "#6B7280"
                                }

                                Button {
                                    text: "↻"
                                    implicitWidth: 26
                                    implicitHeight: 20
                                    padding: 0
                                    onClicked: {
                                        if (viewModel) {
                                            viewModel.rotateCropBox(modelData.listIndex, 90)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
