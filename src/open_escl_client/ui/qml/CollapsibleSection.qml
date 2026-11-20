import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// ============================================================================
// CollapsibleSection.qml: タイトル・サマリー付き再利用可能折りたたみコンテナ
// ============================================================================
Rectangle {
    id: sectionRoot

    property string sectionId: objectName
    property string title: ""
    property string summaryText: ""
    property bool expanded: false
    default property alias contentData: contentColumn.data

    function toggle() {
        var nextVal = !expanded
        expanded = nextVal
        if (viewModel && sectionId.length > 0) {
            viewModel.setSidebarSectionExpanded(sectionId, nextVal)
        }
    }

    Component.onCompleted: {
        if (viewModel && sectionId.length > 0) {
            expanded = viewModel.isSidebarSectionExpanded(sectionId)
        }
    }

    Connections {
        target: viewModel
        function onSidebarSectionsChanged() {
            if (sectionRoot.sectionId.length > 0) {
                sectionRoot.expanded = viewModel.isSidebarSectionExpanded(sectionRoot.sectionId)
            }
        }
    }

    Layout.fillWidth: true
    Layout.minimumWidth: 0
    implicitWidth: 220
    implicitHeight: headerButton.implicitHeight + (expanded ? contentWrapper.implicitHeight : 0)
    color: "#F9FAFB"
    border.color: "#E5E7EB"
    border.width: 1
    radius: 6
    clip: true

    ColumnLayout {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: 0

        Rectangle {
            id: headerButton
            objectName: sectionRoot.objectName ? (sectionRoot.objectName + "Header") : ""
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            implicitHeight: Math.max(30, headerRow.implicitHeight + 10)
            color: headerMouseArea.containsMouse ? "#F3F4F6" : "transparent"
            radius: 6

            RowLayout {
                id: headerRow
                anchors.fill: parent
                anchors.leftMargin: 8
                anchors.rightMargin: 8
                spacing: 6

                Label {
                    text: sectionRoot.expanded ? "▾" : "▸"
                    font.pixelSize: 12
                    font.bold: true
                    color: "#4B5563"
                }

                Label {
                    text: sectionRoot.title
                    font.pixelSize: 12
                    font.bold: true
                    color: "#374151"
                    Layout.fillWidth: sectionRoot.summaryText.length === 0
                    Layout.minimumWidth: 0
                    elide: Text.ElideRight
                }

                Label {
                    visible: sectionRoot.summaryText.length > 0
                    text: sectionRoot.summaryText
                    font.pixelSize: 11
                    color: "#6B7280"
                    horizontalAlignment: Text.AlignRight
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    elide: Text.ElideRight
                }
            }

            MouseArea {
                id: headerMouseArea
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: sectionRoot.toggle()
            }
        }

        Item {
            id: contentWrapper
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            visible: sectionRoot.expanded
            implicitHeight: sectionRoot.expanded ? (contentColumn.implicitHeight + 10) : 0

            ColumnLayout {
                id: contentColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.leftMargin: 8
                anchors.rightMargin: 8
                anchors.topMargin: 2
                spacing: 8
            }
        }
    }
}
