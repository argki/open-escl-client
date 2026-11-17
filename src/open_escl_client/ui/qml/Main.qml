import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// ============================================================================
// Main.qml: アプリケーション全体のメインウィンドウ定義
// ----------------------------------------------------------------------------
// 画面構成:
//   - 上部メイン領域 (RowLayout):
//       1. 左ペイン: ScannerSidebar (スキャナ・用紙サイズ・解像度・保存設定パネル)
//       2. 右ペイン: PreviewArea    (プレビュー画像・自動トリミング枠表示キャンバス)
//   - 下部領域:
//       3. フッター: ActionToolbar  (プレビュー実行・本スキャン保存・ステータスバー)
// ============================================================================
ApplicationWindow {
    id: rootWindow
    width: 1180
    height: 780
    minimumWidth: 920
    minimumHeight: 620
    visible: true
    title: "Open eSCL Client"
    color: "#F3F4F6"

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // --------------------------------------------------------------------
        // 上部メイン領域: 左サイドバー + 中央プレビュー領域
        // --------------------------------------------------------------------
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            // 1. 左サイドバー（スキャナ・読み取り条件・保存設定）
            ScannerSidebar {
                id: scannerSidebar
                Layout.preferredWidth: 340
                Layout.minimumWidth: 300
                Layout.maximumWidth: 400
                Layout.fillHeight: true
            }

            // 区切り線
            Rectangle {
                Layout.preferredWidth: 1
                Layout.fillHeight: true
                color: "#D1D5DB"
            }

            // 2. 中央プレビューキャンバス領域
            PreviewArea {
                id: previewArea
                Layout.fillWidth: true
                Layout.fillHeight: true
            }
        }

        // 区切り線
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: "#D1D5DB"
        }

        // --------------------------------------------------------------------
        // 3. 下部アクションバー（プレビュー・連続スキャン保存ボタン・ステータス表示）
        // --------------------------------------------------------------------
        ActionToolbar {
            id: actionToolbar
            Layout.fillWidth: true
            Layout.preferredHeight: 68
        }
    }
}
