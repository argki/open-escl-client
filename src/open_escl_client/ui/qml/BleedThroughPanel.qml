import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// ============================================================================
// BleedThroughPanel.qml: 薄い紙の両面印刷における裏写り（裏透け）除去設定パネル
// ============================================================================
GroupBox {
    id: bleedThroughPanelRoot
    objectName: "bleedThroughPanel"
    title: "4. 画質・裏写り（裏透け）除去"

    ColumnLayout {
        anchors.fill: parent
        spacing: 8

        CheckBox {
            id: bleedThroughCheckBox
            objectName: "bleedThroughCheckBox"
            text: "両面印刷の裏写り（裏透け）を軽減する"
            Layout.fillWidth: true
            checked: viewModel ? viewModel.bleedThroughEnabled : false
            onToggled: {
                if (viewModel) {
                    viewModel.bleedThroughEnabled = checked
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            enabled: viewModel ? viewModel.bleedThroughEnabled : false

            RowLayout {
                Layout.fillWidth: true
                Label {
                    text: "除去強度"
                    font.bold: true
                    color: "#374151"
                }
                Item { Layout.fillWidth: true }
                Label {
                    objectName: "bleedThroughStrengthValueLabel"
                    readonly property int pctVal: Math.round(bleedThroughStrengthSlider.value)
                    text: pctVal + " %"
                    font.bold: true
                    color: "#1D4ED8"
                }
            }

            Slider {
                id: bleedThroughStrengthSlider
                objectName: "bleedThroughStrengthSlider"
                Layout.fillWidth: true
                from: 0
                to: 100
                stepSize: 5
                snapMode: Slider.SnapAlways
                value: viewModel ? Math.round(viewModel.bleedThroughStrength * 100) : 60
                onPressedChanged: {
                    if (!pressed && viewModel) {
                        viewModel.bleedThroughStrength = Math.round(value) / 100.0
                    }
                }
                onMoved: {
                    if (!pressed && viewModel) {
                        viewModel.bleedThroughStrength = Math.round(value) / 100.0
                    }
                }
            }

            Label {
                text: "強いほど淡い裏透けを除去（上げすぎると薄い文字も薄くなります）"
                font.pixelSize: 11
                color: "#6B7280"
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }

        CheckBox {
            id: bleedThroughWhitenCheckBox
            objectName: "bleedThroughWhitenCheckBox"
            text: "背景の紙色を白く整える"
            Layout.fillWidth: true
            enabled: viewModel ? viewModel.bleedThroughEnabled : false
            checked: viewModel ? viewModel.bleedThroughWhitenBackground : false
            onToggled: {
                if (viewModel) {
                    viewModel.bleedThroughWhitenBackground = checked
                }
            }
        }
    }
}
