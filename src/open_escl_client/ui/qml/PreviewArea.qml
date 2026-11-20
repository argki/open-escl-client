import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// ============================================================================
// PreviewArea.qml: 中央のプレビュー画像・自動トリミング確認キャンバス領域
// ----------------------------------------------------------------------------
// 役割:
//   - スキャンしたプレビュー画像の表示（ズーム・スクロール対応）
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
            id: previewHeaderRow
            Layout.fillWidth: true
            spacing: 8

            Label {
                id: previewTitleLabel
                objectName: "previewTitleLabel"
                text: (viewModel && viewModel.isInspectingCrop)
                    ? ("切り出し結果 #" + (viewModel.selectedCropIndex + 1) + " プレビュー")
                    : "プレビューキャンバス（色合い・自動トリミング確認）"
                font.pixelSize: 14
                font.bold: true
                color: "#1F2937"
                Layout.minimumWidth: 100
                Layout.maximumWidth: implicitWidth
                elide: Text.ElideRight
            }

            Label {
                id: previewSummaryLabel
                objectName: "previewSummaryLabel"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                horizontalAlignment: Text.AlignRight
                elide: Text.ElideRight
                text: viewModel
                      ? (viewModel.selectedPaperSize
                         + " / プレビュー: " + viewModel.previewDpi + " dpi"
                         + (viewModel.hasPreview ? (" (" + viewModel.previewImageWidth + "×" + viewModel.previewImageHeight + ")") : "")
                         + " / 本保存: " + viewModel.selectedResolution + " dpi")
                      : ""
                font.pixelSize: 12
                color: "#4B5563"
            }

            // ズーム操作コントロール
            RowLayout {
                id: zoomControlsRow
                objectName: "zoomControlsRow"
                Layout.fillWidth: false
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
                    text: "下部の「プレビュー」を押すと、指定した解像度（" + (viewModel ? viewModel.selectedPreviewDpi : 100) + " dpi）で読み取り結果を確認できます"
                    font.pixelSize: 13
                    color: "#9CA3AF"
                }
            }

            // プレビュー画像表示領域（ズーム・ホイール・スクロールバー・トリミング枠オーバーレイ対応）
            Flickable {
                id: previewFlickable
                objectName: "previewFlickable"
                anchors.fill: parent
                anchors.margins: 12
                clip: true
                interactive: !cropOverlay.dragging
                boundsBehavior: Flickable.StopAtBounds
                visible: viewModel ? viewModel.hasPreview : false
                contentWidth: Math.max(width, previewCanvasImage.width)
                contentHeight: Math.max(height, previewCanvasImage.height)

                ScrollBar.vertical: ScrollBar {
                    id: previewVScrollBar
                    objectName: "previewVerticalScrollBar"
                    policy: previewFlickable.contentHeight > previewFlickable.height + 1
                            ? ScrollBar.AlwaysOn
                            : ScrollBar.AsNeeded
                }

                ScrollBar.horizontal: ScrollBar {
                    id: previewHScrollBar
                    objectName: "previewHorizontalScrollBar"
                    policy: previewFlickable.contentWidth > previewFlickable.width + 1
                            ? ScrollBar.AlwaysOn
                            : ScrollBar.AsNeeded
                }

                WheelHandler {
                    id: previewWheelHandler
                    objectName: "previewWheelHandler"
                    acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                    property real wheelAccum: 0
                    onWheel: function(event) {
                        if (!viewModel || !viewModel.hasPreview) {
                            return
                        }
                        var delta = event.angleDelta.y !== 0 ? event.angleDelta.y : event.pixelDelta.y
                        if (delta === 0) {
                            return
                        }
                        wheelAccum += delta
                        if (wheelAccum >= 60) {
                            viewModel.zoomInPreview()
                            wheelAccum = 0
                        } else if (wheelAccum <= -60) {
                            viewModel.zoomOutPreview()
                            wheelAccum = 0
                        }
                        event.accepted = true
                    }
                }

                Image {
                    id: previewCanvasImage
                    objectName: "previewCanvasImage"
                    x: Math.max(0, (previewFlickable.contentWidth - width) * 0.5)
                    y: Math.max(0, (previewFlickable.contentHeight - height) * 0.5)
                    width: previewFlickable.width * (viewModel ? viewModel.previewZoom : 1.0)
                    height: previewFlickable.height * (viewModel ? viewModel.previewZoom : 1.0)
                    fillMode: Image.PreserveAspectFit
                    smooth: true
                    mipmap: true
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
                    spacing: 8

                    Label {
                        text: "切り出し・角度補正・自動回転プレビュー (" + (viewModel ? viewModel.cropCount : 0) + "件)"
                        font.pixelSize: 12
                        font.bold: true
                        color: "#374151"
                        Layout.minimumWidth: 0
                        elide: Text.ElideRight
                    }

                    Label {
                        id: cropStripHintLabel
                        objectName: "cropStripHintLabel"
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        horizontalAlignment: Text.AlignRight
                        elide: Text.ElideRight
                        text: "全体像またはカードをクリックして上部キャンバスで拡大確認できます"
                        font.pixelSize: 11
                        color: "#6B7280"
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 10

                    Rectangle {
                        id: fullOverviewCard
                        objectName: "fullOverviewCard"
                        Layout.preferredWidth: 112
                        Layout.fillHeight: true
                        radius: 5
                        color: !(viewModel && viewModel.isInspectingCrop) ? "#EFF6FF" : "#FFFFFF"
                        border.color: !(viewModel && viewModel.isInspectingCrop) ? "#2563EB" : "#D1D5DB"
                        border.width: !(viewModel && viewModel.isInspectingCrop) ? 2 : 1

                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (viewModel) {
                                    viewModel.showFullPreview()
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
                                    text: "全体像"
                                    font.pixelSize: 11
                                    font.bold: true
                                    color: !(viewModel && viewModel.isInspectingCrop) ? "#1D4ED8" : "#374151"
                                }

                                Item { Layout.fillWidth: true }

                                Label {
                                    visible: !(viewModel && viewModel.isInspectingCrop)
                                    text: "表示中"
                                    font.pixelSize: 9
                                    font.bold: true
                                    color: "#2563EB"
                                }
                            }

                            Image {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                fillMode: Image.PreserveAspectFit
                                smooth: true
                                mipmap: true
                                cache: false
                                asynchronous: false
                                source: viewModel ? viewModel.fullPreviewThumbnailUrl : ""
                            }

                            Label {
                                Layout.fillWidth: true
                                horizontalAlignment: Text.AlignHCenter
                                text: "原稿台・枠編集"
                                font.pixelSize: 10
                                color: "#6B7280"
                                elide: Text.ElideRight
                            }
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
                            border.color: (modelData.selected && viewModel && viewModel.isInspectingCrop)
                                ? "#2563EB"
                                : (modelData.selected ? "#10B981" : "#D1D5DB")
                            border.width: modelData.selected ? 2 : 1

                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    if (viewModel) {
                                        viewModel.inspectCropPreview(modelData.listIndex)
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

                                    Label {
                                        visible: modelData.selected && viewModel && viewModel.isInspectingCrop
                                        text: "拡大中"
                                        font.pixelSize: 9
                                        font.bold: true
                                        color: "#2563EB"
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
                                smooth: true
                                mipmap: true
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
}
