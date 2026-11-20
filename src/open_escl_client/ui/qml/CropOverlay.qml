import QtQuick
import QtQuick.Controls

// ============================================================================
// CropOverlay.qml: プレビュー画像上に複数トリミング枠・回転角バッジ・
//                  ドラッグハンドルをオーバーレイ描画する宣言的コンポーネント
// ============================================================================
Item {
    id: overlayRoot
    objectName: "cropOverlay"

    property var cropBoxes: viewModel ? viewModel.cropBoxes : []
    property bool dragging: false

    visible: viewModel ? (viewModel.hasPreview && viewModel.autoCropEnabled && viewModel.cropCount > 0 && !viewModel.isInspectingCrop) : false

    onCropBoxesChanged: overlayCanvas.requestPaint()
    onWidthChanged: overlayCanvas.requestPaint()
    onHeightChanged: overlayCanvas.requestPaint()

    // ------------------------------------------------------------------------
    // 1. 回転矩形ポリゴン描画キャンバス
    // ------------------------------------------------------------------------
    Canvas {
        id: overlayCanvas
        objectName: "cropOverlayCanvas"
        anchors.fill: parent
        antialiasing: true

        onPaint: {
            var ctx = getContext("2d")
            ctx.clearRect(0, 0, width, height)
            if (!overlayRoot.cropBoxes || overlayRoot.cropBoxes.length === 0) {
                return
            }

            for (var i = 0; i < overlayRoot.cropBoxes.length; i++) {
                var box = overlayRoot.cropBoxes[i]
                var pts = box.cornersNorm
                if (!pts || pts.length !== 4) {
                    continue
                }

                var isSelected = !!box.selected
                ctx.beginPath()
                ctx.moveTo(pts[0][0] * width, pts[0][1] * height)
                ctx.lineTo(pts[1][0] * width, pts[1][1] * height)
                ctx.lineTo(pts[2][0] * width, pts[2][1] * height)
                ctx.lineTo(pts[3][0] * width, pts[3][1] * height)
                ctx.closePath()

                // 半透明ハイライト塗りつぶし
                ctx.fillStyle = isSelected
                                ? "rgba(37, 99, 235, 0.14)"
                                : "rgba(16, 185, 129, 0.10)"
                ctx.fill()

                // 枠線（選択枠は太めの青、非選択枠は緑）
                ctx.lineWidth = isSelected ? 2.5 : 1.8
                ctx.strokeStyle = isSelected ? "#2563EB" : "#10B981"
                ctx.stroke()

                // 上辺（TL -> TR）を視覚的に示す控えめな方向マーカー
                var topMidX = ((pts[0][0] + pts[1][0]) * 0.5) * width
                var topMidY = ((pts[0][1] + pts[1][1]) * 0.5) * height
                ctx.beginPath()
                ctx.arc(topMidX, topMidY, isSelected ? 4 : 3, 0, Math.PI * 2)
                ctx.fillStyle = isSelected ? "#1D4ED8" : "#059669"
                ctx.fill()
            }
        }
    }

    // ------------------------------------------------------------------------
    // 2. 各枠のインタラクティブ要素（選択・移動・通し番号/回転角バッジ・四隅ハンドル）
    // ------------------------------------------------------------------------
    Repeater {
        id: cropBoxRepeater
        objectName: "cropBoxRepeater"
        model: overlayRoot.cropBoxes

        delegate: Item {
            id: boxDelegate
            anchors.fill: parent

            readonly property var boxData: modelData
            readonly property int boxListIndex: boxData.listIndex
            readonly property bool isSelected: !!boxData.selected
            readonly property var corners: boxData.cornersNorm || [[0,0],[0,0],[0,0],[0,0]]
            readonly property int effRot: boxData.effectiveRotationDeg || 0
            readonly property bool hasManualRot: (boxData.manualRotationDeg !== undefined && boxData.manualRotationDeg >= 0)

            // 中心付近の移動ドラッグ領域
            Rectangle {
                id: moveAnchor
                width: Math.max(28, Math.min(72, (boxData.widthNorm || 0.1) * overlayRoot.width * 0.45))
                height: Math.max(28, Math.min(72, (boxData.heightNorm || 0.1) * overlayRoot.height * 0.45))
                x: (boxData.centerXNorm || 0.5) * overlayRoot.width - width / 2
                y: (boxData.centerYNorm || 0.5) * overlayRoot.height - height / 2
                radius: 6
                color: isSelected ? "#222563EB" : "transparent"
                border.color: isSelected ? "#662563EB" : "transparent"
                border.width: 1

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.OpenHandCursor
                    property real lastX: 0
                    property real lastY: 0

                    onPressed: function(mouse) {
                        var pt = mapToItem(overlayRoot, mouse.x, mouse.y)
                        lastX = pt.x
                        lastY = pt.y
                        overlayRoot.dragging = true
                        if (viewModel) {
                            viewModel.selectCropBox(boxDelegate.boxListIndex)
                        }
                    }

                    onPositionChanged: function(mouse) {
                        if (!pressed || overlayRoot.width <= 0 || overlayRoot.height <= 0) {
                            return
                        }
                        var pt = mapToItem(overlayRoot, mouse.x, mouse.y)
                        var dxNorm = (pt.x - lastX) / overlayRoot.width
                        var dyNorm = (pt.y - lastY) / overlayRoot.height
                        if (Math.abs(dxNorm) > 0.001 || Math.abs(dyNorm) > 0.001) {
                            lastX = pt.x
                            lastY = pt.y
                            if (viewModel) {
                                viewModel.moveCropBoxByNorm(boxDelegate.boxListIndex, dxNorm, dyNorm)
                            }
                        }
                    }

                    onReleased: overlayRoot.dragging = false
                    onCanceled: overlayRoot.dragging = false
                }
            }

            // 通し番号＋自動/手動回転角バッジ（左上頂点付近）
            Rectangle {
                id: cropBadge
                objectName: "cropBadge_" + boxData.index
                x: Math.max(4, Math.min(overlayRoot.width - width - 4, corners[0][0] * overlayRoot.width + 4))
                y: Math.max(4, Math.min(overlayRoot.height - height - 4, corners[0][1] * overlayRoot.height + 4))
                width: badgeLabel.implicitWidth + 14
                height: 22
                radius: 11
                color: isSelected ? "#2563EB" : "#059669"
                border.color: "#FFFFFF"
                border.width: 1

                Label {
                    id: badgeLabel
                    anchors.centerIn: parent
                    text: "#" + boxData.index + "  " + effRot + "°" + (hasManualRot ? " (手動)" : "")
                    font.pixelSize: 11
                    font.bold: true
                    color: "#FFFFFF"
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        if (viewModel) {
                            viewModel.selectCropBox(boxDelegate.boxListIndex)
                        }
                    }
                }
            }

            // 選択中枠の四隅ドラッグハンドル（TL=0, TR=1, BR=2, BL=3）
            Repeater {
                model: isSelected ? 4 : 0

                delegate: Rectangle {
                    id: cornerHandle
                    objectName: "cropCornerHandle_" + boxData.index + "_" + index
                    readonly property int cornerIdx: index
                    width: 14
                    height: 14
                    radius: 7
                    color: "#FFFFFF"
                    border.color: "#2563EB"
                    border.width: 2.5
                    x: corners[cornerIdx][0] * overlayRoot.width - width / 2
                    y: corners[cornerIdx][1] * overlayRoot.height - height / 2

                    MouseArea {
                        anchors.fill: parent
                        anchors.margins: -4
                        cursorShape: Qt.SizeAllCursor

                        onPressed: {
                            overlayRoot.dragging = true
                            if (viewModel) {
                                viewModel.selectCropBox(boxDelegate.boxListIndex)
                            }
                        }

                        onPositionChanged: function(mouse) {
                            if (!pressed || overlayRoot.width <= 0 || overlayRoot.height <= 0) {
                                return
                            }
                            var pt = mapToItem(overlayRoot, mouse.x, mouse.y)
                            var normX = Math.max(0.0, Math.min(1.0, pt.x / overlayRoot.width))
                            var normY = Math.max(0.0, Math.min(1.0, pt.y / overlayRoot.height))
                            if (viewModel) {
                                viewModel.dragCropCornerToNorm(
                                    boxDelegate.boxListIndex,
                                    cornerHandle.cornerIdx,
                                    normX,
                                    normY
                                )
                            }
                        }

                        onReleased: overlayRoot.dragging = false
                        onCanceled: overlayRoot.dragging = false
                    }
                }
            }
        }
    }
}
