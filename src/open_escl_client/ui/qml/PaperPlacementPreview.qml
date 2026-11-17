import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// ============================================================================
// PaperPlacementPreview.qml: 原稿台に対する用紙の置き方イメージ図コンポーネント
// ----------------------------------------------------------------------------
// 役割:
//   - 外側の大きな四角: 原稿台（スキャナガラス面全体: bedWidthMm × bedHeightMm）
//   - 内側の四角:       原稿（用紙: paperWidthMm × paperHeightMm）
//   - 原稿台の四角の中に、原稿の四角がどの向き（縦置き / 横置き）・どの大きさで
//     置かれるかを要素の重なりなく明快に図解する
// ============================================================================
Rectangle {
    id: previewCard
    objectName: "paperPlacementPreview"
    implicitWidth: 340
    implicitHeight: 430
    width: 340
    height: 430
    radius: 10
    color: "#FFFFFF"
    border.color: "#94A3B8"
    border.width: 1

    // ViewModel の getPlacementGuide() または currentPlacementGuide から渡される辞書
    property var guideData: viewModel ? viewModel.currentPlacementGuide : ({})

    readonly property string paperLabel: guideData && guideData.label ? guideData.label : ""
    readonly property string orientation: guideData && guideData.orientation ? guideData.orientation : "portrait"
    readonly property real paperW: guideData && guideData.paperWidthMm ? guideData.paperWidthMm : 210
    readonly property real paperH: guideData && guideData.paperHeightMm ? guideData.paperHeightMm : 297
    readonly property real bedW: guideData && guideData.bedWidthMm ? guideData.bedWidthMm : 216
    readonly property real bedH: guideData && guideData.bedHeightMm ? guideData.bedHeightMm : 297
    readonly property real wRatio: guideData && guideData.widthRatio ? guideData.widthRatio : 0.97
    readonly property real hRatio: guideData && guideData.heightRatio ? guideData.heightRatio : 1.0
    readonly property string hintText: guideData && guideData.placementHint ? guideData.placementHint : ""

    // ------------------------------------------------------------------------
    // 1. 上部ヘッダー領域（タイトル・用紙名・原稿台サイズ表記）
    // ------------------------------------------------------------------------
    Column {
        id: headerSection
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: 14
        spacing: 4

        Row {
            width: parent.width
            spacing: 8

            Label {
                text: "原稿台への置き方イメージ"
                font.pixelSize: 12
                font.bold: true
                color: "#475569"
            }

            Label {
                text: "（原稿台: " + Math.round(previewCard.bedW) + "×" + Math.round(previewCard.bedH) + " mm）"
                font.pixelSize: 11
                color: "#64748B"
            }
        }

        Label {
            id: placementGuideTitleLabel
            objectName: "placementGuideTitleLabel"
            width: parent.width
            text: previewCard.paperLabel
            font.pixelSize: 14
            font.bold: true
            color: "#0F172A"
            elide: Text.ElideRight
        }
    }

    // ------------------------------------------------------------------------
    // 2. 中央の図解ステージ領域（固定高さ 270px: 原稿台の四角の中に原稿の四角を描画）
    // ------------------------------------------------------------------------
    Rectangle {
        id: diagramContainer
        objectName: "diagramContainer"
        anchors.top: headerSection.bottom
        anchors.topMargin: 10
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: 14
        anchors.rightMargin: 14
        height: 270
        radius: 6
        color: "#F8FAFC"
        border.color: "#E2E8F0"
        border.width: 1

        // 基準角の凡例ラベル（上部外側）
        Label {
            id: originLegendLabel
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.topMargin: 6
            anchors.leftMargin: 10
            text: "● 左上が原稿台の基準角（原点）"
            font.pixelSize: 11
            color: "#DC2626"
        }

        // 図解描画キャンバス領域（凡例ラベルの下）
        Item {
            id: bedStageArea
            anchors.top: originLegendLabel.bottom
            anchors.topMargin: 6
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 10
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: 16
            anchors.rightMargin: 16

            readonly property real availW: Math.max(80, width)
            readonly property real availH: Math.max(80, height)
            readonly property real bedAspect: previewCard.bedW / Math.max(1.0, previewCard.bedH)
            readonly property real computedBedH: Math.min(availH, availW / Math.max(0.1, bedAspect))
            readonly property real computedBedW: computedBedH * bedAspect

            // ================================================================
            // 外側の四角: 原稿台（スキャナガラス面全体）
            // ================================================================
            Rectangle {
                id: scannerBedRect
                objectName: "scannerBedRect"
                width: bedStageArea.computedBedW
                height: bedStageArea.computedBedH
                anchors.centerIn: parent
                color: "#E2E8F0"
                border.color: "#475569"
                border.width: 2
                radius: 2

                // 原稿台の中央下部に薄く「原稿台（ガラス面）」と表示
                Label {
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.bottom: parent.bottom
                    anchors.bottomMargin: 4
                    text: "原稿台（ガラス面）"
                    font.pixelSize: 10
                    color: "#64748B"
                    visible: previewCard.hRatio < 0.88
                }

                // ============================================================
                // 内側の四角: 原稿（実際に置く用紙の四角）
                // ============================================================
                Rectangle {
                    id: paperSheetRect
                    objectName: "paperSheetRect"
                    x: 2
                    y: 2
                    width: Math.max(28, (scannerBedRect.width - 4) * previewCard.wRatio)
                    height: Math.max(28, (scannerBedRect.height - 4) * previewCard.hRatio)
                    color: "#EFF6FF"
                    border.color: "#0284C7"
                    border.width: 2
                    radius: 1

                    // 原稿の上辺を示すライン（どの辺が上か一目でわかるガイド）
                    Rectangle {
                        anchors.top: parent.top
                        anchors.left: parent.left
                        anchors.right: parent.right
                        height: 4
                        color: "#0284C7"
                    }

                    // 原稿の四角の中央ラベル（向き・寸法）
                    Column {
                        anchors.centerIn: parent
                        spacing: 3
                        width: parent.width - 6

                        Label {
                            width: parent.width
                            horizontalAlignment: Text.AlignHCenter
                            text: previewCard.orientation === "landscape"
                                  ? "原稿（横置き）"
                                  : (previewCard.orientation === "maximum" ? "原稿台全体" : "原稿（縦置き）")
                            font.pixelSize: paperSheetRect.width < 80 ? 10 : 12
                            font.bold: true
                            color: "#0369A1"
                            elide: Text.ElideRight
                        }

                        Label {
                            width: parent.width
                            horizontalAlignment: Text.AlignHCenter
                            text: Math.round(previewCard.paperW) + " × " + Math.round(previewCard.paperH) + " mm"
                            font.pixelSize: paperSheetRect.width < 80 ? 9 : 11
                            color: "#0284C7"
                            elide: Text.ElideRight
                            visible: paperSheetRect.height >= 42
                        }
                    }
                }

                // 左上の基準角（赤い丸印）
                Rectangle {
                    width: 10
                    height: 10
                    radius: 5
                    color: "#DC2626"
                    x: -4
                    y: -4
                }
            }
        }
    }

    // ------------------------------------------------------------------------
    // 3. 下部の置き方ガイド説明文領域
    // ------------------------------------------------------------------------
    Label {
        id: placementGuideHintLabel
        objectName: "placementGuideHintLabel"
        anchors.top: diagramContainer.bottom
        anchors.topMargin: 10
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: 14
        anchors.rightMargin: 14
        anchors.bottomMargin: 10
        text: previewCard.hintText
        wrapMode: Text.Wrap
        font.pixelSize: 11
        color: "#334155"
        verticalAlignment: Text.AlignTop
    }
}
