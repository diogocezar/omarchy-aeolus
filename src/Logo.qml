import QtQuick

// The Aeolus mark: three swept blades around a hub, drawn from inline SVG in
// `color`, so the bar icon takes the bar's color. `spin` turns it, one turn
// every `period` ms (the panel header spins at the fans' pace).
Image {
    id: root

    property color color: "white"
    property real size: 16
    property bool spin: false
    property int period: 2000

    readonly property string blade:
        'M50 50 C46 36 50 18 64 10 C76 16 76 32 66 42 C61 47 55 49 50 50 Z'

    width: size
    height: size
    sourceSize.width: Math.ceil(size * 2)
    sourceSize.height: Math.ceil(size * 2)
    fillMode: Image.PreserveAspectFit
    smooth: true
    mipmap: true

    source: "data:image/svg+xml;utf8," + encodeURIComponent(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><g fill="' + String(color) + '">' +
        '<path d="' + blade + '"/>' +
        '<path d="' + blade + '" transform="rotate(120 50 50)"/>' +
        '<path d="' + blade + '" transform="rotate(240 50 50)"/>' +
        '<circle cx="50" cy="50" r="9"/></g></svg>')

    RotationAnimator on rotation {
        running: root.spin && root.visible
        from: 0
        to: 360
        duration: Math.max(250, root.period)
        loops: Animation.Infinite
    }
    onSpinChanged: if (!spin) rotation = 0
}
