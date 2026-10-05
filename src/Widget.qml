import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Bar icon + popup for Aeolus, built on the shell's native panel kit. The
// popup never touches the hardware: the root service (system/aeolus.py, run
// by systemd) drives the fan headers and writes what it's doing to
// /run/aeolus/status.json, which is read here; a mode change goes through
// `aeolus mode`, which only writes a one-line request the service checks.
Panel {
    id: root
    moduleName: "diogocezar.aeolus"  // must match manifest id
    ipcTarget: "diogocezar.aeolus"

    readonly property string language: setting("language", "auto")
    onLanguageChanged: Strings.language = language

    readonly property color  foreground: bar ? bar.foreground : Color.foreground
    readonly property color  barForeground: bar ? bar.barForeground : Color.foreground
    readonly property color  dim: Qt.darker(foreground, 1.55)
    readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

    readonly property string cli: "/usr/local/bin/aeolus"
    // must match system/aeolus.py VERSION; a mismatch means the plugin was
    // updated but the installed service copy wasn't
    readonly property string pluginVersion: "1.0.0"
    readonly property bool outdated: online && status.version !== pluginVersion
    readonly property string installCommand:
        "sudo " + localPath("../system/install.sh").replace(Quickshell.env("HOME"), "~")
    readonly property string startCommand: "sudo systemctl enable --now aeolus"

    function localPath(relativePath) {
        var url = Qt.resolvedUrl(relativePath).toString();
        return url.startsWith("file://") ? decodeURIComponent(url.substring(7)) : url;
    }

    // --- Service state ----------------------------------------------------------
    property var status: null            // parsed status.json, null when offline
    property bool installed: false       // /etc/aeolus/config.json exists
    property string error: ""
    // the mode just asked for, shown until the service reports it
    property string pending: ""

    readonly property bool online: status !== null
    readonly property string mode: pending !== "" ? pending.split(" ")[0] : (online ? status.mode : "")
    readonly property int manual: pending.indexOf("manual ") === 0 ? parseInt(pending.split(" ")[1])
                                : (online && status.manual !== null ? status.manual : -1)
    readonly property string reason: online ? status.reason : ""
    readonly property bool alarm: reason !== ""
    readonly property var headers: online ? status.headers : []
    readonly property var fanHeaders: headers.filter(function (h) { return h.role === "fan" })
    readonly property int fanDuty: fanHeaders.length ? fanHeaders[0].duty : 0
    readonly property int fanRpm: {
        var top = 0;
        for (var i = 0; i < fanHeaders.length; i++) top = Math.max(top, fanHeaders[i].rpm || 0);
        return top;
    }

    function degrees(v) { return v === null || v === undefined ? "—" : Math.round(v) + "°C" }

    readonly property string summary: !online ? Strings.t("offline")
        : alarm ? Strings.t("reason_" + reason)
        : Strings.t("mode_" + mode) + (mode === "manual" && manual >= 0 ? " " + manual + "%" : "")
          + " · " + Strings.t("cpu") + " " + degrees(status.cpu)

    function parseStatus(raw) {
        try {
            var st = JSON.parse(raw);
            if (!st || typeof st !== "object" || !Array.isArray(st.headers)
                    || Date.now() / 1000 - Number(st.updated) > 10) {
                root.status = null;
                return;
            }
            root.status = st;
            var want = root.pending;
            if (want !== "" && (want === st.mode || want === "manual " + st.manual)) root.pending = "";
        } catch (e) {
            root.status = null;
        }
    }

    FileView {
        id: statusFile
        path: "/run/aeolus/status.json"
        printErrors: false
        onLoaded: root.parseStatus(text())
        onLoadFailed: root.status = null
    }
    FileView {
        id: configFile
        path: "/etc/aeolus/config.json"
        printErrors: false
        onLoaded: root.installed = true
        onLoadFailed: root.installed = false
    }
    // faster while the popup is open; the service writes once a second
    Timer {
        interval: root.opened ? 1000 : 4000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: {
            statusFile.reload();
            if (!root.online) configFile.reload();
        }
    }

    Process {
        id: modeProc
        onExited: function (code) {
            root.error = code === 0 ? "" : Strings.t("error");
            if (code !== 0) root.pending = "";
            statusFile.reload();
        }
    }
    function setMode(text) {
        root.pending = text;
        root.error = "";
        modeProc.running = false;
        modeProc.command = [root.cli, "mode"].concat(text.split(" "));
        modeProc.running = true;
    }
    // slider: one request per pause, not one per pixel
    property int sliderValue: -1
    Timer {
        id: sliderDebounce
        interval: 180
        onTriggered: if (root.sliderValue >= 0) root.setMode("manual " + root.sliderValue)
    }

    Process { id: copyProc }
    property bool copied: false
    Timer { id: copiedReset; interval: 1600; onTriggered: root.copied = false }
    function copy(text) {
        copyProc.command = ["wl-copy", "--", text];
        copyProc.running = true;
        root.copied = true;
        copiedReset.restart();
    }

    readonly property var modes: ["auto", "silent", "performance", "manual"]
    function cycleMode(step) {
        var i = Math.max(0, modes.indexOf(mode));
        var next = modes[(i + step + modes.length) % modes.length];
        setMode(next === "manual" ? "manual " + Math.max(fanDuty, 20) : next);
    }

    Component.onCompleted: Strings.language = language

    onOpenedChanged: if (opened) {
        if (panelFlick) panelFlick.contentY = 0;
        statusFile.reload();
        Qt.callLater(function () { keyCatcher.forceActiveFocus() });
    }

    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight

    BarIconButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        useActiveColor: false
        tooltipText: Strings.t("tooltip", root.summary)
        onPressed: function (b) { root.toggle() }

        iconComponent: Component {
            Logo {
                size: Math.round(Style.bar.iconCanvas * 0.9)
                color: root.alarm ? Color.urgent
                     : root.online ? root.barForeground : Qt.darker(root.barForeground, 1.9)
            }
        }
    }

    KeyboardPanel {
        id: panel
        anchorItem: button
        owner: root
        bar: root.bar
        open: root.opened
        focusTarget: keyCatcher
        contentWidth: panel.fittedContentWidth(Style.space(360))
        contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(820))

        PanelKeyCatcher {
            id: keyCatcher
            anchors.fill: parent

            onCloseRequested: root.close()
            onTabRequested: function (direction) { root.switchPanel(direction) }
            onMoveRequested: function (dx, dy) {
                if (dx !== 0 && root.online) root.cycleMode(dx);
                if (dy !== 0)
                    panelFlick.contentY = Math.max(0, Math.min(panelFlick.contentY + dy * Style.space(56),
                        panelFlick.contentHeight - panelFlick.height));
            }

            Flickable {
                id: panelFlick
                anchors.fill: parent
                contentWidth: width
                contentHeight: column.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                flickableDirection: Flickable.VerticalFlick
                interactive: contentHeight > height
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                Column {
                    id: column
                    width: panelFlick.width
                    spacing: Style.space(12)

                    PanelHero {
                        width: parent.width
                        title: "Aeolus"
                        meta: root.summary
                        foreground: root.foreground
                        fontFamily: root.fontFamily
                        iconOpacity: root.online ? 1.0 : 0.45

                        iconComponent: Component {
                            Logo {
                                size: Style.font.display * 1.25
                                color: root.alarm ? Color.urgent : root.foreground
                                // turns faster as the fans do
                                spin: root.online && root.fanRpm > 0
                                period: Math.round(4200 - 34 * Math.min(100, root.fanDuty))
                            }
                        }
                    }

                    Text {
                        visible: root.alarm || root.error !== ""
                        width: parent.width
                        textFormat: Text.PlainText
                        text: root.alarm ? Strings.t("reason_" + root.reason) : root.error
                        color: Color.urgent
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.body
                        font.bold: true
                        wrapMode: Text.WordWrap
                    }

                    // --- Not running (or an old service copy): how to fix it
                    Column {
                        visible: !root.online || root.outdated
                        width: parent.width
                        spacing: Style.space(8)

                        Text {
                            width: parent.width
                            textFormat: Text.PlainText
                            text: root.outdated ? Strings.t("outdated", root.status.version)
                                : Strings.t(root.installed ? "offlineStopped" : "offlineHint")
                            color: root.foreground
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.body
                            wrapMode: Text.WordWrap
                        }
                        Rectangle {
                            width: parent.width
                            implicitHeight: cmdText.implicitHeight + Style.space(14)
                            radius: Style.cornerRadius
                            color: Style.selectedFillFor(root.foreground, Color.accent)
                            Text {
                                id: cmdText
                                anchors.fill: parent
                                anchors.margins: Style.space(7)
                                textFormat: Text.PlainText
                                text: root.installed && !root.outdated ? root.startCommand : root.installCommand
                                color: root.foreground
                                font.family: root.fontFamily
                                font.pixelSize: Style.font.caption
                                wrapMode: Text.WrapAnywhere
                            }
                        }
                        Button {
                            width: parent.width
                            text: root.copied ? Strings.t("copied") : Strings.t("copy")
                            bordered: true
                            foreground: root.foreground
                            fontFamily: root.fontFamily
                            onClicked: root.copy(root.installed && !root.outdated ? root.startCommand : root.installCommand)
                        }
                    }

                    // --- Temperatures
                    Column {
                        visible: root.online
                        width: parent.width
                        spacing: Style.space(8)

                        SectionHeader { width: parent.width; title: Strings.t("temperatures") }
                        TempRow {
                            width: parent.width
                            label: Strings.t("cpu")
                            value: root.online ? root.status.cpu : null
                            note: !root.online ? ""
                                : (root.mode === "auto" ? Strings.t("target", root.status.target + "°C") + " · " : "")
                                  + Strings.t("limit", root.status.emergency + "°C")
                            limit: root.online ? root.status.emergency : 90
                        }
                        TempRow {
                            visible: root.online && root.status.gpu !== null && root.status.gpu !== undefined
                            width: parent.width
                            label: Strings.t("gpu")
                            value: root.online ? root.status.gpu : null
                            note: ""
                            limit: 87
                        }
                    }

                    PanelSeparator { visible: root.online; width: parent.width; foreground: root.foreground }

                    // --- Mode: 2 x 2
                    Column {
                        visible: root.online
                        width: parent.width
                        spacing: Style.space(8)

                        SectionHeader { width: parent.width; title: Strings.t("mode") }
                        Grid {
                            id: modeGrid
                            width: parent.width
                            columns: 2
                            columnSpacing: Style.space(8)
                            rowSpacing: Style.space(8)
                            Repeater {
                                model: root.modes
                                delegate: Button {
                                    required property var modelData
                                    width: (modeGrid.width - modeGrid.columnSpacing) / 2
                                    text: Strings.t("mode_" + modelData)
                                    selected: root.mode === modelData
                                    bordered: true
                                    foreground: root.foreground
                                    fontFamily: root.fontFamily
                                    onClicked: root.setMode(modelData === "manual"
                                        ? "manual " + Math.max(root.fanDuty, 20) : modelData)
                                }
                            }
                        }
                        Text {
                            width: parent.width
                            textFormat: Text.PlainText
                            text: root.online ? Strings.t("hint_" + root.mode, root.status.target + "°C") : ""
                            color: root.dim
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.caption
                            wrapMode: Text.WordWrap
                        }
                    }

                    // --- Speed slider (fans; moving it switches to manual)
                    Column {
                        visible: root.online && root.fanHeaders.length > 0
                        width: parent.width
                        spacing: Style.space(4)

                        SectionHeader {
                            width: parent.width
                            title: Strings.t("speed")
                            valueText: (speedSlider.dragging ? Math.round(speedSlider.liveValue) : root.fanDuty) + "%"
                        }
                        PanelSlider {
                            id: speedSlider
                            bar: root.bar
                            width: parent.width
                            minimum: 0
                            maximum: 100
                            step: 5
                            integer: true
                            value: root.mode === "manual" && root.manual >= 0 ? root.manual : root.fanDuty
                            onMoved: function (v) {
                                root.sliderValue = Math.round(v);
                                sliderDebounce.restart();
                            }
                        }
                        Text {
                            width: parent.width
                            textFormat: Text.PlainText
                            text: Strings.t("speedHint")
                            color: root.dim
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.caption
                            wrapMode: Text.WordWrap
                        }
                    }

                    PanelSeparator { visible: root.online; width: parent.width; foreground: root.foreground }

                    // --- Headers
                    Column {
                        visible: root.online
                        width: parent.width
                        spacing: Style.space(6)

                        SectionHeader { width: parent.width; title: Strings.t("headers") }
                        Repeater {
                            model: root.headers
                            delegate: HeaderRow {
                                required property var modelData
                                header: modelData
                                width: column.width
                            }
                        }
                        Text {
                            width: parent.width
                            topPadding: Style.space(6)
                            textFormat: Text.PlainText
                            text: root.online ? Strings.t("rails", root.status.emergency + "°C") : ""
                            color: root.dim
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.caption
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }
        }
    }

    // --- Components -------------------------------------------------------------

    component SectionHeader: Item {
        id: section
        property string title: ""
        property string valueText: ""
        implicitHeight: Math.max(header.implicitHeight, valueLabel.implicitHeight)

        PanelSectionHeader {
            id: header
            anchors.left: parent.left
            anchors.right: valueLabel.left
            anchors.rightMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            text: section.title.toUpperCase()
            foreground: root.foreground
            fontFamily: root.fontFamily
        }
        Text {
            id: valueLabel
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            visible: section.valueText !== ""
            textFormat: Text.PlainText
            text: section.valueText
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: true
        }
    }

    // A temperature, big, with a meter that fills toward the emergency limit.
    component TempRow: Column {
        id: temp
        property string label: ""
        property var value: null
        property string note: ""
        property real limit: 90
        readonly property real fraction: value === null ? 0 : Math.max(0, Math.min(1, (value - 25) / (limit - 25)))
        readonly property color tone: value !== null && value >= limit - 5 ? Color.urgent : root.foreground
        spacing: Style.space(4)

        Item {
            width: parent.width
            implicitHeight: big.implicitHeight
            Text {
                anchors.left: parent.left
                anchors.baseline: big.baseline
                textFormat: Text.PlainText
                text: temp.label
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
            }
            Text {
                id: noteText
                anchors.right: big.left
                anchors.rightMargin: Style.space(10)
                anchors.baseline: big.baseline
                textFormat: Text.PlainText
                text: temp.note
                color: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
            }
            Text {
                id: big
                anchors.right: parent.right
                textFormat: Text.PlainText
                text: root.degrees(temp.value)
                color: temp.tone
                font.family: root.fontFamily
                font.pixelSize: Style.font.display * 0.8
                font.bold: true
            }
        }
        Rectangle {
            width: parent.width
            height: Math.max(4, Style.space(4))
            radius: height / 2
            color: Style.selectedFillFor(root.foreground, Color.accent)
            Rectangle {
                width: parent.width * temp.fraction
                height: parent.height
                radius: parent.radius
                color: temp.tone
                Behavior on width { NumberAnimation { duration: 400; easing.type: Easing.OutCubic } }
            }
        }
    }

    // One header: name (from its role), duty and rpm.
    component HeaderRow: Item {
        id: row
        property var header: ({})
        readonly property string name: header.label === "Pump" ? Strings.t("pump")
                                      : header.label === "Fans" ? Strings.t("fans") : header.label
        implicitHeight: Math.max(Style.space(30), nameText.implicitHeight + Style.space(8))

        Logo {
            id: icon
            anchors.left: parent.left
            anchors.leftMargin: Style.space(4)
            anchors.verticalCenter: parent.verticalCenter
            size: Style.space(16)
            color: row.header.stalled ? Color.urgent : root.dim
            spin: (row.header.rpm || 0) > 0
            period: Math.round(4200 - 34 * Math.min(100, row.header.duty || 0))
        }
        Text {
            id: nameText
            anchors.left: icon.right
            anchors.leftMargin: Style.space(10)
            anchors.right: numbers.left
            anchors.rightMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            textFormat: Text.PlainText
            text: row.name
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            elide: Text.ElideRight
        }
        Text {
            id: numbers
            anchors.right: parent.right
            anchors.rightMargin: Style.space(4)
            anchors.verticalCenter: parent.verticalCenter
            textFormat: Text.PlainText
            text: row.header.stalled ? Strings.t("stopped")
                : (row.header.duty + "% · " + (row.header.rpm === null ? "—" : row.header.rpm + " rpm"))
            color: row.header.stalled ? Color.urgent : root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            font.bold: row.header.stalled
        }
    }
}
