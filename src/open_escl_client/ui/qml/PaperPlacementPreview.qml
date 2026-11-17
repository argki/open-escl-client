import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// ============================================================================
// PaperPlacementPreview.qml: 原稿台に対する用紙の置き方イメージ図コンポーネント
// ----------------------------------------------------------------------------
// 役割:
//   - 原稿台（スキャナガラス面全体: bedWidthMm × bedHeightMm）の外枠と基準角を表示
//   - 選択・ホバー中の用紙（paperWidthMm × paperHeightMm）が原稿台に対して
//     どの向き（縦置き / 横置き）・どのサイズ比率（widthRatio × heightRatio）で
//     配置されるかを視覚的な図解として描画する
// ============================================================================
Rectangle {
    id: previewCard
    objectName: "paperPlacementPreview"
    width: 300
    height: 350
    radius: 8
    color: "#FFFFFF"
    border.color: "#CBD5E1"
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

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 8

        // 1. ヘッダー（選択/ホバー中の用紙サイズ名）
        Label {
            Layout.fillWidth: true
            text: "原稿台への置き方イメージ"
            font.pixelSize: 12
            font.bold: true
            color: "#64748B"
        }

        Label {
            objectName: "placementGuideTitleLabel"
            Layout.fillWidth: true
            text: previewCard.paperLabel
            font.pixelSize: 14
            font.bold: true
            color: "#0F172A"
            elide: Text.ElideRight
        }

        // 2. 原稿台（ガラス面）と用紙の図解ステージ
        Item {
            id: diagramContainer
            Layout.fillWidth: true
            Layout.fillHeight: true

            // 原稿台の縦横比を保って中央に配置
            readonly property real maxDrawW: width - 16
            readonly property real maxDrawH: height - 16
            readonly property real bedAspect: previewCard.bedW / Math.max(1, previewCard.bedH)
            readonly property real drawBedH: Math.min(maxDrawH, maxDrawW / Math.max(0.1, bedAspect))
            readonly property real drawBedW: drawBedH * bedAspect

            // 原稿台（スキャナガラス面全体）の外枠
            Rectangle {
                id: scannerBedRect
                objectName: "scannerBedRect"
                width: diagramContainer.drawBedW
                height: diagramContainer.drawBedH
                anchors.centerIn: parent
                color: "#F1F5F9"
                border.color: "#64748B"
                border.width: 2
                radius: 3

                // 原稿台全体のサイズ表記（右下）
                Label {
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    anchors.margins: 4
                    text: "原稿台: " + Math.round(previewCard.bedW) + "×" + Math.round(previewCard.bedH) + " mm"
                    font.pixelSize: 10
                    color: "#64748B"
                }

                // 実際に置く用紙の矩形（左上基準角に揃えて描画）
                Rectangle {
                    id: paperSheetRect
                    objectName: "paperSheetRect"
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.leftMargin: 2
                    anchors.topMargin: 2
                    width: Math.max(24, (parent.width - 4) * previewCard.wRatio)
                    height: Math.max(24, (parent.height - 4) * previewCard.hRatio)
                    color: previewCard.orientation === "landscape" ? "#DBEAFE" : "#E0F2FE"
                    border.color: "#0284C7"
                    border.width: 2
                    radius: 2

                    ColumnLayout {
                        anchors.centerIn: parent
                        spacing: 2
                        width: parent.width - 8

                        // 向きのバッジ表示
                        Label {
                            Layout.alignment: Qt.AlignHCenter
                            text: previewCard.orientation === "landscape"
                                  ? "↔ 横置き"
                                  : (previewCard.orientation === "maximum" ? "■ 全体" : "↕ 縦置き")
                            font.pixelSize: 12
                            font.bold: true
                            color: "#0369A1"
                        }

                        // 上辺（幅）× 縦辺（高さ）の寸法表示
                        Label {
                            Layout.alignment: Qt.AlignHCenter
                            text: Math.round(previewCard.paperW) + " × " + Math.round(previewCard.paperH) + " mm"
                            font.pixelSize: 10
                            color: "#0284C7"
                        }

                        // 原稿の向きをイメージしやすいダミー文字ライン
                        Rectangle {
                            Layout.alignment: Qt.AlignHCenter
                            Layout.preferredWidth: Math.min(parent.width * 0.65, 70)
                            Layout.preferredHeight: 2
                            color: "#38BDF8"
                            visible: paperSheetRect.height > 55
                        }
                        Rectangle {
                            Layout.alignment: Qt.AlignHCenter
                            Layout.preferredWidth: Math.min(parent.width * 0.5, 52)
                            Layout.preferredHeight: 2
                            color: "#7DD3FC"
                            visible: paperSheetRect.height > 70
                        }
                    }
                }

                // 左上の基準角（▲マーク）
                Rectangle {
                    width: 18
                    height: 18
                    radius: 9
                    color: "#DC2626"
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.leftMargin: -7
                    anchors.topMargin: -7

                    Label {
                        anchors.centerIn: parent
                        text: "↖"
                        font.pixelSize: 11
                        font.bold: true
                        color: "#FFFFFF"
                    }
                }
            }
        }

        // 3. 置き方のテキストガイド
        Label {
            objectName: "placementGuideHintLabel"
            Layout.fillWidth: true
            text: previewCard.hintText
            wrapMode: Text.Wrap
            font.pixelSize: 11
            color: "#334155"
        }
    }
}
