import QtQuick

// The Aeolus mark: five swept fan blades and a hub inside the fan's frame,
// drawn from inline SVG in `color`, so the bar icon takes the bar's color.
// `spin` turns it, one turn every `period` ms (the panel header spins at the
// fans' pace).
Image {
    id: root

    property color color: "white"
    property real size: 16
    property bool spin: false
    property int period: 2000

    readonly property string blade:
        'M50 50 C43 43 39 30 44 16 C51 8 64 10 70 17 C63 23 59 31 57 41 C56 45 53 48 50 50 Z'

    width: size
    height: size
    sourceSize.width: Math.ceil(size * 2)
    sourceSize.height: Math.ceil(size * 2)
    fillMode: Image.PreserveAspectFit
    smooth: true
    mipmap: true

    source: "data:image/svg+xml;utf8," + encodeURIComponent(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">' +
        '<circle cx="50" cy="50" r="44" fill="none" stroke="' + String(color) + '" stroke-width="7"/>' +
        '<g fill="' + String(color) + '">' +
        [0, 72, 144, 216, 288].map(function (a) {
            return '<path d="' + blade + '" transform="rotate(' + a + ' 50 50)"/>';
        }).join('') +
        '<circle cx="50" cy="50" r="8"/></g></svg>')

    RotationAnimator on rotation {
        running: root.spin && root.visible
        from: 0
        to: 360
        duration: Math.max(250, root.period)
        loops: Animation.Infinite
    }
    onSpinChanged: if (!spin) rotation = 0
}
