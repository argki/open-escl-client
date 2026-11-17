import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// ============================================================================
// ScannerSidebar.qml: 左側のスキャン設定・保存設定サイドバー
// ----------------------------------------------------------------------------
// セクション構成:
//   1. スキャナ選択セクション（eSCLスキャナ一覧・再検索ボタン）
//   2. 読み取り設定セクション（用紙サイズ［A4/B5/最大］・解像度DPI）
//   3. 保存設定セクション（保存先フォルダ・ネーミングルール・ファイル形式）
// ============================================================================
Rectangle {
    id: sidebarRoot
    color: "#FFFFFF"

    ScrollView {
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth

        ColumnLayout {
            width: parent.width
            spacing: 16

            Item { Layout.preferredHeight: 4 }

            // ----------------------------------------------------------------
            // セクション 1: スキャナ選択
            // ----------------------------------------------------------------
            GroupBox {
                title: "1. スキャナ選択 (eSCL / AirScan)"
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 8

                    ComboBox {
                        id: scannerCombo
                        objectName: "scannerCombo"
                        Layout.fillWidth: true
                        model: viewModel ? viewModel.scanners : []
                        onActivated: function(index) {
                            if (viewModel && model[index]) {
                                viewModel.selectedScanner = model[index]
                            }
                        }
                    }

                    Button {
                        id: refreshScannerButton
                        objectName: "refreshScannerButton"
                        text: "スキャナを再検索"
                        Layout.fillWidth: true
                        onClicked: {
                            if (viewModel) {
                                viewModel.refreshScanners()
                            }
                        }
                    }
                }
            }

            // ----------------------------------------------------------------
            // セクション 2: 読み取りサイズ・解像度設定
            // ----------------------------------------------------------------
            GroupBox {
                title: "2. 読み取り設定（サイズ・解像度）"
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 10

                    Label {
                        text: "用紙サイズ"
                        font.bold: true
                        color: "#374151"
                    }

                    ComboBox {
                        id: paperSizeCombo
                        objectName: "paperSizeCombo"
                        Layout.fillWidth: true
                        model: viewModel ? viewModel.paperSizes : []
                        onActivated: function(index) {
                            if (viewModel && model[index]) {
                                viewModel.selectedPaperSize = model[index]
                            }
                        }
                    }

                    Label {
                        text: "保存時の解像度 (DPI)"
                        font.bold: true
                        color: "#374151"
                    }

                    ComboBox {
                        id: resolutionCombo
                        objectName: "resolutionCombo"
                        Layout.fillWidth: true
                        model: viewModel ? viewModel.resolutions : []
                        currentIndex: 1
                        onActivated: function(index) {
                            if (viewModel && model[index] !== undefined) {
                                viewModel.selectedResolution = model[index]
                            }
                        }
                    }
                }
            }

            // ----------------------------------------------------------------
            // セクション 3: 保存設定（フォルダ・ネーミングルール・形式）
            // ----------------------------------------------------------------
            GroupBox {
                title: "3. 保存設定（フォルダ・命名規則・形式）"
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 10

                    Label {
                        text: "デフォルト保存先フォルダ"
                        font.bold: true
                        color: "#374151"
                    }

                    TextField {
                        id: saveDirField
                        objectName: "saveDirField"
                        Layout.fillWidth: true
                        text: viewModel ? viewModel.saveDirectory : ""
                        placeholderText: "~/Pictures/Scans"
                        onEditingFinished: {
                            if (viewModel) {
                                viewModel.saveDirectory = text
                            }
                        }
                    }

                    Label {
                        text: "ネーミングルール（日付・連番）"
                        font.bold: true
                        color: "#374151"
                    }

                    TextField {
                        id: namingRuleField
                        objectName: "namingRuleField"
                        Layout.fillWidth: true
                        text: viewModel ? viewModel.namingRule : ""
                        placeholderText: "Scan_{date}_{seq:03d}"
                        onEditingFinished: {
                            if (viewModel) {
                                viewModel.namingRule = text
                            }
                        }
                    }

                    Label {
                        text: "保存ファイル形式"
                        font.bold: true
                        color: "#374151"
                    }

                    ComboBox {
                        id: saveFormatCombo
                        objectName: "saveFormatCombo"
                        Layout.fillWidth: true
                        model: viewModel ? viewModel.saveFormats : []
                        onActivated: function(index) {
                            if (viewModel && model[index]) {
                                viewModel.selectedSaveFormat = model[index]
                            }
                        }
                    }
                }
            }

            Item { Layout.fillHeight: true }
        }
    }
}
