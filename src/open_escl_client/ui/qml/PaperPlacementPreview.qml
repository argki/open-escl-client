import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// ============================================================================
// PaperPlacementPreview.qml: 原稿台に対する用紙の置き方イメージ図コンポーネント
// ----------------------------------------------------------------------------
// 物理的な原稿台（正面から見た図）:
//   - 横方向（左右）が長辺（例: MFC-J6580CDW では 432 mm）、奥行き方向（上下）が短辺（297 mm）
//   - 上辺が「奥（カバーヒンジ側）」、下辺が「手前（正面・操作パネル側）」
//   - 基準点（原点マーク）は「左奥（左上角）」
//   - 外側の横長の四角（原稿台）の中に、内側の四角（原稿: 縦置き / 横置き）を描画する
// ============================================================================
Rectangle {
    id: previewCard
    objectName: "paperPlacementPreview"
    implicitWidth: 380
    implicitHeight: 370
    width: 380
    height: 370
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
    readonly property real bedW: guideData && guideData.bedWidthMm ? guideData.bedWidthMm : 432
    readonly property real bedH: guideData && guideData.bedHeightMm ? guideData.bedHeightMm : 297
    readonly property real wRatio: guideData && guideData.widthRatio ? guideData.widthRatio : 0.49
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
            spacing: 6

            Label {
                text: "原稿台への置き方イメージ（正面から見た図）"
                font.pixelSize: 12
                font.bold: true
                color: "#475569"
            }

            Label {
                text: "原稿台: 横" + Math.round(previewCard.bedW) + "×奥行" + Math.round(previewCard.bedH) + "mm"
                font.pixelSize: 11
                color: "#64748B"
            }
        }

        Label {
            id: placementGuideTitleLabel
            objectName: "placementGuideTitleLabel"
            width: parent.width
            text: previewCard.paperLabel
            font.pixelSize: 13
            font.bold: true
            color: "#0F172A"
            elide: Text.ElideRight
        }
    }

    // ------------------------------------------------------------------------
    // 2. 中央の図解ステージ領域（固定高さ 220px: 横長の原稿台の四角の中に原稿の四角を描画）
    // ------------------------------------------------------------------------
    Rectangle {
        id: diagramContainer
        objectName: "diagramContainer"
        anchors.top: headerSection.bottom
        anchors.topMargin: 8
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: 14
        anchors.rightMargin: 14
        height: 220
        radius: 6
        color: "#F8FAFC"
        border.color: "#E2E8F0"
        border.width: 1

        // 奥側ラベルと左奥基準点の説明（上部外側）
        Row {
            id: backSideHeader
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.topMargin: 6
            anchors.leftMargin: 14
            anchors.rightMargin: 14
            spacing: 12

            Label {
                text: "↘ 基準点マーク（左奥）"
                font.pixelSize: 11
                font.bold: true
                color: "#DC2626"
            }

            Label {
                text: "【奥側（カバーヒンジ側）】"
                font.pixelSize: 11
                color: "#64748B"
            }
        }

        // 手前（正面）ラベル（下部外側）
        Label {
            id: frontSideFooter
            anchors.bottom: parent.bottom
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottomMargin: 5
            text: "▲ 手前（正面・操作パネル側）"
            font.pixelSize: 11
            color: "#64748B"
        }

        // 図解描画キャンバス領域（奥側ラベルと手前ラベルの間）
        Item {
            id: bedStageArea
            anchors.top: backSideHeader.bottom
            anchors.topMargin: 6
            anchors.bottom: frontSideFooter.top
            anchors.bottomMargin: 6
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: 16
            anchors.rightMargin: 16

            readonly property real availW: Math.max(120, width)
            readonly property real availH: Math.max(80, height)
            // 横方向（bedW）が長く、奥行き方向（bedH）が短い横長アスペクト比
            readonly property real bedAspect: previewCard.bedW / Math.max(1.0, previewCard.bedH)
            readonly property real computedBedW: Math.min(availW, availH * bedAspect)
            readonly property real computedBedH: computedBedW / Math.max(0.1, bedAspect)

            // ================================================================
            // 外側の四角: 原稿台（正面から見て横方向が長く、奥行きが短いガラス面）
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

                // 原稿台ガラス面の右下に薄く「原稿台ガラス面」と表示
                Label {
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.rightMargin: 6
                    anchors.bottomMargin: 4
                    text: "原稿台ガラス面"
                    font.pixelSize: 10
                    color: "#64748B"
                    visible: previewCard.wRatio < 0.85 || previewCard.hRatio < 0.85
                }

                // ============================================================
                // 内側の四角: 原稿（左奥の基準点に合わせて置かれる用紙の四角）
                // ============================================================
                Rectangle {
                    id: paperSheetRect
                    objectName: "paperSheetRect"
                    x: 2
                    y: 2
                    width: Math.max(32, (scannerBedRect.width - 4) * previewCard.wRatio)
                    height: Math.max(32, (scannerBedRect.height - 4) * previewCard.hRatio)
                    color: "#EFF6FF"
                    border.color: "#0284C7"
                    border.width: 2
                    radius: 1

                    // 原稿の四角の中央ラベル（向き・寸法）
                    Column {
                        anchors.centerIn: parent
                        spacing: 2
                        width: parent.width - 6

                        Label {
                            width: parent.width
                            horizontalAlignment: Text.AlignHCenter
                            text: previewCard.orientation === "landscape"
                                  ? "原稿（横置き）"
                                  : (previewCard.orientation === "maximum" ? "原稿台全体" : "原稿（縦置き）")
                            font.pixelSize: paperSheetRect.width < 90 ? 10 : 12
                            font.bold: true
                            color: "#0369A1"
                            elide: Text.ElideRight
                        }

                        Label {
                            width: parent.width
                            horizontalAlignment: Text.AlignHCenter
                            text: "横" + Math.round(previewCard.paperW) + " × 奥行" + Math.round(previewCard.paperH) + " mm"
                            font.pixelSize: paperSheetRect.width < 90 ? 9 : 10
                            color: "#0284C7"
                            elide: Text.ElideRight
                            visible: paperSheetRect.height >= 40
                        }
                    }
                }

                // 左奥の基準点マーク（赤い丸印）
                Rectangle {
                    width: 12
                    height: 12
                    radius: 6
                    color: "#DC2626"
                    x: -5
                    y: -5
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
        anchors.topMargin: 8
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
