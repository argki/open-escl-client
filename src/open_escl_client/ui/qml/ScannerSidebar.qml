import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// ============================================================================
// ScannerSidebar.qml: 左側のスキャン設定・保存設定サイドバー
// ----------------------------------------------------------------------------
// セクション構成:
//   1. スキャナ選択セクション（mDNS自動検索・IP/URL直接指定）
//   2. 読み取り設定セクション（給紙元［原稿台/ADF］・用紙サイズ・解像度DPI）
//      ※ 用紙サイズにマウスポインタをかざすと「原稿台への置き方イメージ図」を表示
//   3. 保存設定セクション（保存先フォルダ・ネーミングルール・ファイル形式）
// ============================================================================
Rectangle {
    id: sidebarRoot
    color: "#FFFFFF"

    // ドロップダウン項目やコンボボックス上でホバー中の用紙サイズラベル
    property string hoveredPaperLabel: ""
    readonly property bool showPlacementGuide: paperSizeCombo.hovered || hoveredPaperLabel.length > 0

    ScrollView {
        anchors.fill: parent
        clip: true
        contentWidth: availableWidth

        ColumnLayout {
            width: parent.width
            spacing: 16

            Item { Layout.preferredHeight: 4 }

            // ----------------------------------------------------------------
            // セクション 1: スキャナ選択 (mDNS 自動検出 & IP/URL 直接指定)
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
                        currentIndex: {
                            if (!viewModel || !model) return 0
                            var idx = model.indexOf(viewModel.selectedScanner)
                            return idx >= 0 ? idx : 0
                        }
                        onActivated: function(index) {
                            if (viewModel && model[index]) {
                                viewModel.selectedScanner = model[index]
                            }
                        }
                    }

                    Button {
                        id: refreshScannerButton
                        objectName: "refreshScannerButton"
                        text: "スキャナを再検索 (mDNS / Bonjour)"
                        Layout.fillWidth: true
                        onClicked: {
                            if (viewModel) {
                                viewModel.refreshScanners()
                            }
                        }
                    }

                    // IPアドレス / URL 直接接続（mDNSが届かない環境用フォールバック）
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6

                        TextField {
                            id: manualHostField
                            objectName: "manualHostField"
                            Layout.fillWidth: true
                            placeholderText: "IP または URL (例: 192.168.1.50)"
                            onAccepted: {
                                if (viewModel && text.length > 0) {
                                    viewModel.addManualScanner(text)
                                }
                            }
                        }

                        Button {
                            id: connectManualButton
                            objectName: "connectManualButton"
                            text: "接続"
                            onClicked: {
                                if (viewModel && manualHostField.text.length > 0) {
                                    viewModel.addManualScanner(manualHostField.text)
                                }
                            }
                        }
                    }
                }
            }

            // ----------------------------------------------------------------
            // セクション 2: 読み取り設定（給紙元・用紙サイズ・解像度）
            // ----------------------------------------------------------------
            GroupBox {
                title: "2. 読み取り設定（給紙元・サイズ・解像度）"
                Layout.fillWidth: true
                Layout.leftMargin: 16
                Layout.rightMargin: 16

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 10

                    Label {
                        text: "給紙元（原稿台 / ADF）"
                        font.bold: true
                        color: "#374151"
                    }

                    ComboBox {
                        id: inputSourceCombo
                        objectName: "inputSourceCombo"
                        Layout.fillWidth: true
                        model: viewModel ? viewModel.inputSources : []
                        currentIndex: {
                            if (!viewModel || !model) return 0
                            var idx = model.indexOf(viewModel.selectedInputSource)
                            return idx >= 0 ? idx : 0
                        }
                        onActivated: function(index) {
                            if (viewModel && model[index]) {
                                viewModel.selectedInputSource = model[index]
                            }
                        }
                    }

                    Label {
                        text: "用紙サイズ・向き（マウスをかざすと配置図を表示）"
                        font.bold: true
                        color: "#374151"
                    }

                    ComboBox {
                        id: paperSizeCombo
                        objectName: "paperSizeCombo"
                        hoverEnabled: true
                        Layout.fillWidth: true
                        model: viewModel ? viewModel.paperSizes : []
                        currentIndex: {
                            if (!viewModel || !model) return 0
                            var idx = model.indexOf(viewModel.selectedPaperSize)
                            return idx >= 0 ? idx : 0
                        }
                        onActivated: function(index) {
                            if (viewModel && model[index]) {
                                viewModel.selectedPaperSize = model[index]
                            }
                        }
                        popup.onClosed: {
                            sidebarRoot.hoveredPaperLabel = ""
                        }

                        // ドロップダウン内の各選択肢にマウスポインタをかざした際にも配置図をリアルタイム更新
                        delegate: ItemDelegate {
                            width: paperSizeCombo.width
                            text: modelData
                            highlighted: paperSizeCombo.highlightedIndex === index
                            hoverEnabled: true
                            onHoveredChanged: {
                                if (hovered) {
                                    sidebarRoot.hoveredPaperLabel = modelData
                                } else if (sidebarRoot.hoveredPaperLabel === modelData) {
                                    sidebarRoot.hoveredPaperLabel = ""
                                }
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
                        currentIndex: {
                            if (!viewModel || !model) return 0
                            var idx = model.indexOf(viewModel.selectedResolution)
                            return idx >= 0 ? idx : 0
                        }
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

    // ------------------------------------------------------------------------
    // ホバー時にサイドバー右隣へ表示される「原稿台への置き方イメージ図」ポップアップ
    // ------------------------------------------------------------------------
    Popup {
        id: placementGuidePopup
        objectName: "placementGuidePopup"
        parent: sidebarRoot
        x: sidebarRoot.width + 12
        y: Math.max(16, paperSizeCombo.mapToItem(sidebarRoot, 0, 0).y - 20)
        padding: 0
        modal: false
        focus: false
        closePolicy: Popup.NoAutoClose
        visible: sidebarRoot.showPlacementGuide

        background: Item {}

        contentItem: PaperPlacementPreview {
            id: placementPreviewCard
            guideData: {
                if (!viewModel) return ({})
                var targetLabel = sidebarRoot.hoveredPaperLabel.length > 0
                                  ? sidebarRoot.hoveredPaperLabel
                                  : viewModel.selectedPaperSize
                return viewModel.getPlacementGuide(targetLabel)
            }
        }
    }
}
