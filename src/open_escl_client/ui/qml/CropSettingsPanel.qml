import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// ============================================================================
// CropSettingsPanel.qml: 自動トリミング・余白微調整・微細角度調整・
//                        90°/180°/270° 自動向き補正＆手動回転パネル
// ============================================================================
GroupBox {
    id: cropPanelRoot
    objectName: "cropSettingsPanel"
    title: "3. トリミング・角度・自動回転"

    ColumnLayout {
        anchors.fill: parent
        spacing: 8

        CheckBox {
            id: autoCropCheckBox
            objectName: "autoCropCheckBox"
            text: "自動トリミングを有効にする"
            checked: viewModel ? viewModel.autoCropEnabled : true
            onToggled: {
                if (viewModel) {
                    viewModel.autoCropEnabled = checked
                }
            }
        }

        CheckBox {
            id: multiDocumentCheckBox
            objectName: "multiDocumentCheckBox"
            text: "複数枚の原稿を自動分割する"
            enabled: viewModel ? viewModel.autoCropEnabled : true
            checked: viewModel ? viewModel.multiDocumentEnabled : true
            onToggled: {
                if (viewModel) {
                    viewModel.multiDocumentEnabled = checked
                }
            }
        }

        CheckBox {
            id: autoOrientationCheckBox
            objectName: "autoOrientationCheckBox"
            text: "自動向き補正 (90°/180°/270°)"
            enabled: viewModel ? viewModel.autoCropEnabled : true
            checked: viewModel ? viewModel.autoOrientationEnabled : true
            onToggled: {
                if (viewModel) {
                    viewModel.autoOrientationEnabled = checked
                }
            }
        }

        // 検出パターン（原稿タイプ：自動判別 / レシート・複数小原稿 / 単票・定型紙 / 全面）
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4
            enabled: viewModel ? viewModel.autoCropEnabled : true

            Label {
                text: "検出パターン（原稿タイプ）"
                font.bold: true
                color: "#374151"
                Layout.fillWidth: true
                elide: Text.ElideRight
            }

            ComboBox {
                id: detectionPresetComboBox
                objectName: "detectionPresetComboBox"
                Layout.fillWidth: true
                model: viewModel ? viewModel.detectionPresets : []
                currentIndex: {
                    if (!viewModel || !model) return 0
                    var idx = model.indexOf(viewModel.selectedDetectionPreset)
                    return idx >= 0 ? idx : 0
                }
                onActivated: function(index) {
                    if (viewModel && model[index]) {
                        viewModel.selectedDetectionPreset = model[index]
                    }
                }
            }

            Label {
                id: resolvedPresetLabel
                objectName: "resolvedPresetLabel"
                visible: viewModel ? (viewModel.hasPreview && viewModel.autoCropEnabled) : false
                text: viewModel ? viewModel.resolvedDetectionPresetLabel : ""
                font.pixelSize: 11
                font.bold: true
                color: "#1D4ED8"
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
        }

        // 検出感度（-5〜+5: プラスで薄い紙のフチや白地を広く検出、マイナスで影やゴミを抑制）
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            enabled: viewModel ? viewModel.autoCropEnabled : true

            RowLayout {
                Layout.fillWidth: true
                Label {
                    text: viewModel && viewModel.selectedCropIndex >= 0
                          ? ("選択枠 #" + (viewModel.selectedCropIndex + 1) + " の検出感度")
                          : "全体の検出感度"
                    font.bold: true
                    color: "#374151"
                }
                Item { Layout.fillWidth: true }
                Label {
                    objectName: "detectionSensitivityValueLabel"
                    readonly property int sensVal: Math.round(detectionSensitivitySlider.value)
                    text: (sensVal > 0 ? "+" : "") + sensVal + (sensVal === 0 ? " (標準)" : (sensVal > 0 ? " (高感度)" : " (控えめ)"))
                    font.bold: true
                    color: sensVal > 0 ? "#1D4ED8" : (sensVal < 0 ? "#B45309" : "#4B5563")
                }
            }

            Slider {
                id: detectionSensitivitySlider
                objectName: "detectionSensitivitySlider"
                Layout.fillWidth: true
                from: -5
                to: 5
                stepSize: 1
                snapMode: Slider.SnapAlways
                value: viewModel ? viewModel.selectedCropSensitivity : 0
                onPressedChanged: {
                    if (!pressed && viewModel) {
                        viewModel.selectedCropSensitivity = Math.round(value)
                    }
                }
                onMoved: {
                    if (!pressed && viewModel) {
                        viewModel.selectedCropSensitivity = Math.round(value)
                    }
                }
            }

            Label {
                text: "プラスで薄い紙の境界を広く検出、マイナスで影・ゴミを抑制"
                font.pixelSize: 11
                color: "#6B7280"
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }

        // 検出比率（規格：自動判別 / A判・B判(白銀比) / はがき / L判写真 等）
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4
            enabled: viewModel ? viewModel.autoCropEnabled : true

            Label {
                text: viewModel && viewModel.selectedCropIndex >= 0
                      ? ("選択枠 #" + (viewModel.selectedCropIndex + 1) + " の検出比率（規格）")
                      : "全体の検出比率（規格）"
                font.bold: true
                color: "#374151"
                Layout.fillWidth: true
                elide: Text.ElideRight
            }

            ComboBox {
                id: aspectRatioPresetComboBox
                objectName: "aspectRatioPresetComboBox"
                Layout.fillWidth: true
                model: viewModel ? viewModel.aspectRatioPresets : []
                currentIndex: {
                    if (!viewModel || !model) return 0
                    var idx = model.indexOf(viewModel.selectedCropAspectRatioPreset)
                    return idx >= 0 ? idx : 0
                }
                onActivated: function(index) {
                    if (viewModel && model[index]) {
                        viewModel.selectedCropAspectRatioPreset = model[index]
                    }
                }
            }
        }

        // 余白の一括微調整（mm: 負値で内側に削る／正値で外側に広げる）
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            enabled: viewModel ? viewModel.autoCropEnabled : true

            RowLayout {
                Layout.fillWidth: true
                Label {
                    text: "余白の微調整 (mm)"
                    font.bold: true
                    color: "#374151"
                }
                Item { Layout.fillWidth: true }
                Label {
                    objectName: "cropMarginValueLabel"
                    readonly property real mmVal: viewModel ? viewModel.cropMarginMm : 0.0
                    text: (mmVal > 0 ? "+" : "") + mmVal.toFixed(1) + " mm"
                    font.bold: true
                    color: mmVal < 0 ? "#B45309" : (mmVal > 0 ? "#1D4ED8" : "#4B5563")
                }
            }

            Slider {
                id: cropMarginSlider
                objectName: "cropMarginSlider"
                Layout.fillWidth: true
                from: -5.0
                to: 5.0
                stepSize: 0.5
                value: viewModel ? viewModel.cropMarginMm : 0.0
                onMoved: {
                    if (viewModel) {
                        viewModel.cropMarginMm = value
                    }
                }
            }

            Label {
                text: "マイナスでフチの余白・影を除去、プラスで周囲を拡張"
                font.pixelSize: 11
                color: "#6B7280"
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: "#E5E7EB"
        }

        // 選択中のトリミング枠に対する微細角度調整＆90°手動回転
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 6
            enabled: viewModel ? (viewModel.autoCropEnabled && viewModel.cropCount > 0) : false

            RowLayout {
                Layout.fillWidth: true
                Label {
                    text: viewModel && viewModel.selectedCropIndex >= 0
                          ? ("選択枠 #" + (viewModel.selectedCropIndex + 1) + " の角度調整")
                          : "選択枠の角度調整"
                    font.bold: true
                    color: "#374151"
                }
                Item { Layout.fillWidth: true }
                Label {
                    objectName: "fineAngleValueLabel"
                    readonly property real angVal: viewModel ? viewModel.selectedCropFineAngle : 0.0
                    text: (angVal > 0 ? "+" : "") + angVal.toFixed(1) + "°"
                    font.bold: true
                    color: "#2563EB"
                }
            }

            Slider {
                id: fineAngleSlider
                objectName: "fineAngleSlider"
                Layout.fillWidth: true
                from: -10.0
                to: 10.0
                stepSize: 0.1
                value: viewModel ? viewModel.selectedCropFineAngle : 0.0
                onMoved: {
                    if (viewModel) {
                        viewModel.setSelectedCropFineAngle(value)
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 6

                Button {
                    id: rotateLeftButton
                    objectName: "rotateLeftButton"
                    text: "↺ 左90°"
                    Layout.fillWidth: true
                    onClicked: {
                        if (viewModel) {
                            viewModel.rotateSelectedCropLeft()
                        }
                    }
                }

                Label {
                    objectName: "selectedRotationLabel"
                    text: (viewModel ? viewModel.selectedCropRotationDeg : 0) + "°"
                    font.bold: true
                    color: "#1F2937"
                    horizontalAlignment: Text.AlignHCenter
                    Layout.preferredWidth: 42
                }

                Button {
                    id: rotateRightButton
                    objectName: "rotateRightButton"
                    text: "↻ 右90°"
                    Layout.fillWidth: true
                    onClicked: {
                        if (viewModel) {
                            viewModel.rotateSelectedCropRight()
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 6

                Button {
                    id: resetCropButton
                    objectName: "resetCropButton"
                    text: "調整リセット"
                    Layout.fillWidth: true
                    onClicked: {
                        if (viewModel) {
                            viewModel.resetSelectedCropAdjustments()
                        }
                    }
                }

                Button {
                    id: redetectCropButton
                    objectName: "redetectCropButton"
                    text: "枠を再検出"
                    Layout.fillWidth: true
                    enabled: viewModel ? (viewModel.hasPreview && viewModel.autoCropEnabled) : false
                    onClicked: {
                        if (viewModel) {
                            viewModel.redetectCropBoxes()
                        }
                    }
                }
            }
        }
    }
}
