import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

// ============================================================================
// SaveSettingsPanel.qml: 保存先フォルダ・ネーミングルール・形式・圧縮品質設定パネル
// ----------------------------------------------------------------------------
// 配置要素:
//   - デフォルト保存先フォルダ入力 + ネイティブフォルダ選択ダイアログボタン
//   - ネーミングルール入力（{date}, {time}, {seq:03d} 等）+ 次のファイル名プレビュー
//   - ファイル形式選択（PDF / JPEG / PNG）
//   - 圧縮品質スライダー（10% 〜 100%）
//   - マルチページPDF結合モード切り替え（PDF選択時）
// ============================================================================
GroupBox {
    id: savePanelRoot
    objectName: "saveSettingsPanel"
    title: "5. 保存設定（フォルダ・命名規則・形式・圧縮率）"

    FolderDialog {
        id: saveFolderDialog
        objectName: "saveFolderDialog"
        title: "デフォルトの保存先フォルダを選択"
        onAccepted: {
            if (viewModel) {
                viewModel.setSaveDirectoryFromUrl(selectedFolder.toString())
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 10

        // --------------------------------------------------------------------
        // デフォルト保存先フォルダ
        // --------------------------------------------------------------------
        Label {
            text: "デフォルト保存先フォルダ"
            font.bold: true
            color: "#374151"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            elide: Text.ElideRight
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            spacing: 6

            TextField {
                id: saveDirField
                objectName: "saveDirField"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                text: viewModel ? viewModel.saveDirectory : ""
                placeholderText: "~/Pictures/Scans"
                onTextEdited: {
                    if (viewModel && text.trim().length > 0) {
                        viewModel.saveDirectory = text
                    }
                }
                onEditingFinished: {
                    if (viewModel) {
                        viewModel.saveDirectory = text
                    }
                }
            }

            Button {
                id: chooseFolderButton
                objectName: "chooseFolderButton"
                Layout.minimumWidth: 0
                text: "参照..."
                onClicked: saveFolderDialog.open()
            }
        }

        // --------------------------------------------------------------------
        // ネーミングルール & 次の保存ファイル名リアルタイムプレビュー
        // --------------------------------------------------------------------
        Label {
            text: "ネーミングルール（{date} / {time} / {seq:03d}）"
            font.bold: true
            color: "#374151"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            elide: Text.ElideRight
        }

        TextField {
            id: namingRuleField
            objectName: "namingRuleField"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            text: viewModel ? viewModel.namingRule : ""
            placeholderText: "Scan_{date}_{time}_{seq:03d}"
            onTextEdited: {
                if (viewModel) {
                    viewModel.namingRule = text
                }
            }
            onEditingFinished: {
                if (viewModel) {
                    viewModel.namingRule = text
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            implicitHeight: previewFileRow.implicitHeight + 10
            radius: 4
            color: "#F3F4F6"
            border.color: "#E5E7EB"

            RowLayout {
                id: previewFileRow
                anchors.fill: parent
                anchors.leftMargin: 8
                anchors.rightMargin: 8
                spacing: 4

                Label {
                    text: "次のファイル名:"
                    font.pixelSize: 11
                    color: "#6B7280"
                }

                Label {
                    id: nextFilenamePreviewLabel
                    objectName: "nextFilenamePreviewLabel"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    Layout.preferredWidth: 0
                    text: viewModel ? viewModel.nextFilenamePreview : ""
                    font.pixelSize: 11
                    font.bold: true
                    color: "#1D4ED8"
                    elide: Text.ElideMiddle
                }
            }
        }

        CheckBox {
            id: ocrNamingCheckBox
            objectName: "ocrNamingCheckBox"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            text: "OCRでタイトル・日付をファイル名に使う"
            checked: viewModel ? viewModel.ocrNamingEnabled : false
            onToggled: {
                if (viewModel) {
                    viewModel.ocrNamingEnabled = checked
                }
            }
        }

        ColumnLayout {
            id: ocrNamingDetailsBox
            objectName: "ocrNamingDetailsBox"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            spacing: 6
            visible: viewModel ? viewModel.ocrNamingEnabled : false

            RowLayout {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                spacing: 4

                Button {
                    id: insertOcrTitleTokenButton
                    objectName: "insertOcrTitleTokenButton"
                    text: "+タイトル"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    onClicked: {
                        if (viewModel) {
                            viewModel.insertNamingToken("{ocr_title}")
                        }
                    }
                }

                Button {
                    id: insertOcrDateTokenButton
                    objectName: "insertOcrDateTokenButton"
                    text: "+日付"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    onClicked: {
                        if (viewModel) {
                            viewModel.insertNamingToken("{ocr_date}")
                        }
                    }
                }

                Button {
                    id: applyOcrPresetButton
                    objectName: "applyOcrPresetButton"
                    text: "OCR標準"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    onClicked: {
                        if (viewModel) {
                            viewModel.applyOcrNamingPreset()
                        }
                    }
                }
            }

            Button {
                id: extractOcrNamingButton
                objectName: "extractOcrNamingButton"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                enabled: viewModel ? (viewModel.hasPreview && !viewModel.ocrBusy && !viewModel.isBusy) : false
                text: (viewModel && viewModel.ocrBusy) ? "OCR解析中..." : "プレビューから候補を抽出"
                onClicked: {
                    if (viewModel) {
                        viewModel.extractOcrNamingFromPreview()
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                spacing: 6

                TextField {
                    id: ocrTitleField
                    objectName: "ocrTitleField"
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    placeholderText: "タイトル候補"
                    text: viewModel ? viewModel.ocrTitle : ""
                    onTextEdited: {
                        if (viewModel) {
                            viewModel.ocrTitle = text
                        }
                    }
                }

                TextField {
                    id: ocrDateField
                    objectName: "ocrDateField"
                    Layout.preferredWidth: 96
                    Layout.minimumWidth: 0
                    placeholderText: "YYYYMMDD"
                    text: viewModel ? viewModel.ocrDate : ""
                    onTextEdited: {
                        if (viewModel) {
                            viewModel.ocrDate = text
                        }
                    }
                }
            }
        }

        // --------------------------------------------------------------------
        // 保存ファイル形式
        // --------------------------------------------------------------------
        Label {
            text: "保存ファイル形式"
            font.bold: true
            color: "#374151"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            elide: Text.ElideRight
        }

        ComboBox {
            id: saveFormatCombo
            objectName: "saveFormatCombo"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            model: viewModel ? viewModel.saveFormats : []
            currentIndex: {
                if (!viewModel || !model) return 0
                var idx = model.indexOf(viewModel.selectedSaveFormat)
                return idx >= 0 ? idx : 0
            }
            onActivated: function(index) {
                if (viewModel && model[index]) {
                    viewModel.selectedSaveFormat = model[index]
                }
            }
        }

        // --------------------------------------------------------------------
        // 圧縮品質スライダー（JPEG / WebP / PDF画質 / PNG圧縮度）
        // --------------------------------------------------------------------
        RowLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            visible: viewModel ? viewModel.supportsQuality : true

            Label {
                text: {
                    if (!viewModel) return "圧縮品質 (JPEG / PDF画質)"
                    if (viewModel.selectedSaveFormat === "PNG") return "画質・圧縮バランス"
                    if (viewModel.selectedSaveFormat === "WebP") return "圧縮品質 (WebP画質)"
                    return "圧縮品質 (JPEG / PDF画質)"
                }
                font.bold: true
                color: "#374151"
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                elide: Text.ElideRight
            }

            Label {
                objectName: "jpegQualityValueLabel"
                text: viewModel ? (viewModel.jpegQuality + "%") : "85%"
                font.bold: true
                color: "#1F2937"
            }
        }

        Slider {
            id: jpegQualitySlider
            objectName: "jpegQualitySlider"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            visible: viewModel ? viewModel.supportsQuality : true
            from: 10
            to: 100
            stepSize: 5
            value: viewModel ? viewModel.jpegQuality : 85
            onMoved: {
                if (viewModel) {
                    viewModel.jpegQuality = Math.round(value)
                }
            }
        }

        // --------------------------------------------------------------------
        // マルチページ結合モード（PDF / TIFF選択時）
        // --------------------------------------------------------------------
        CheckBox {
            id: multipagePdfCheckBox
            objectName: "multipagePdfCheckBox"
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            visible: viewModel ? viewModel.supportsMultipage : true
            checked: viewModel ? viewModel.multipagePdf : false
            text: "複数スキャンを1つのPDF/TIFFファイルに結合"
            onToggled: {
                if (viewModel) {
                    viewModel.multipagePdf = checked
                }
            }
        }
    }
}
