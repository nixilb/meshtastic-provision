"""Light and dark looks of the window, and the button that switches them.

Qt's Fusion style is used so both looks render the same on every desktop;
the palettes below are the usual Fusion ones. The choice is kept with
QSettings (`~/.config/meshtastic-provision/meshtastic-provision.conf`);
until the user chooses, the desktop's own scheme is followed.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSettings, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QProxyStyle, QSizePolicy, QStyle, QStyleFactory, QToolBar, QWidget

LIGHT = "light"
DARK = "dark"


def _dark_palette() -> QPalette:
    palette = QPalette()
    base = QColor(35, 35, 35)
    window = QColor(53, 53, 53)
    text = QColor(220, 220, 220)
    highlight = QColor(42, 130, 218)
    palette.setColor(QPalette.ColorRole.Window, window)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, base)
    palette.setColor(QPalette.ColorRole.AlternateBase, window)
    palette.setColor(QPalette.ColorRole.ToolTipBase, base)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, window)
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 80, 80))
    palette.setColor(QPalette.ColorRole.Link, highlight)
    palette.setColor(QPalette.ColorRole.Highlight, highlight)
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(140, 140, 140))
    palette.setColor(QPalette.ColorRole.Mid, QColor(140, 140, 140))
    disabled = QColor(127, 127, 127)
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, disabled)
    return palette


def _light_palette() -> QPalette:
    palette = QApplication.style().standardPalette()
    palette.setColor(QPalette.ColorRole.Mid, QColor(110, 110, 110))
    return palette


def system_scheme() -> str:
    """The desktop's scheme, `dark` or `light`."""
    hints = QApplication.styleHints()
    return DARK if hints.colorScheme() == Qt.ColorScheme.Dark else LIGHT


def current_scheme() -> str:
    saved = QSettings().value("theme", "")
    return saved if saved in (LIGHT, DARK) else system_scheme()


# The progress bar of meshtastic-desktop (iced's progress_bar, girth 6):
# a thin rounded bar in the Meshtastic green on a discreet track. Colours
# are the app's own (`src/theme.rs`: `primary` on `border`), per scheme.
_PROGRESS = {
    DARK: ("#67ea94", "#2c3934"),
    LIGHT: ("#208a4c", "#ced9d4"),
}
_PROGRESS_STYLE = """
QProgressBar {{
    max-height: 6px;
    min-height: 6px;
    border: none;
    border-radius: 3px;
    background: {track};
}}
QProgressBar::chunk {{
    border-radius: 3px;
    background: {bar};
}}
"""


class _Style(QProxyStyle):
    """Fusion, without the desktop theme's icons on dialog buttons (a red
    cross on No/Cancel/Discard, a green tick on Yes/OK): plain buttons like
    every other one of the window."""

    def __init__(self) -> None:
        super().__init__(QStyleFactory.create("Fusion"))

    def styleHint(self, hint, option=None, widget=None, returnData=None):  # noqa: N802 - Qt naming
        if hint == QStyle.StyleHint.SH_DialogButtonBox_ButtonsHaveIcons:
            return 0
        return super().styleHint(hint, option, widget, returnData)


def apply_scheme(scheme: str) -> None:
    app = QApplication.instance()
    app.setStyle(_Style())
    app.setPalette(_dark_palette() if scheme == DARK else _light_palette())
    # An application style sheet (not one on the widget) so switching the
    # scheme restyles the bar; it only matches progress bars.
    bar, track = _PROGRESS[scheme]
    app.setStyleSheet(_PROGRESS_STYLE.format(bar=bar, track=track))


def install(window) -> None:
    """Apply the saved or system scheme and add the switch to `window`'s
    toolbar."""
    apply_scheme(current_scheme())
    toolbar = QToolBar("Look")
    toolbar.setMovable(False)
    window.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
    action = QAction(window)
    action.setToolTip("Switch between the light and dark looks")

    def refresh() -> None:
        dark = current_scheme() == DARK
        # Sun when dark (press for light), moon when light: the icon shows
        # what the button gives. Drawn here, so it never depends on the
        # desktop's icon theme.
        color = QApplication.palette().color(QPalette.ColorRole.WindowText)
        action.setIcon(_sun(color) if dark else _moon(color))
        action.setText("Light" if dark else "Dark")

    def toggle() -> None:
        scheme = LIGHT if current_scheme() == DARK else DARK
        QSettings().setValue("theme", scheme)
        apply_scheme(scheme)
        refresh()

    action.triggered.connect(toggle)
    refresh()
    spacer = QWidget()  # pushes the switch to the right end
    spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    toolbar.addWidget(spacer)
    toolbar.addAction(action)


def _canvas(size: int = 24) -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    return pixmap, painter


def _sun(color: QColor) -> QIcon:
    pixmap, painter = _canvas()
    painter.setPen(QPen(color, 2))
    painter.setBrush(color)
    painter.drawEllipse(QPointF(12, 12), 4.5, 4.5)
    for i in range(8):
        angle = i * math.pi / 4
        painter.drawLine(
            QPointF(12 + 7.5 * math.cos(angle), 12 + 7.5 * math.sin(angle)),
            QPointF(12 + 10.5 * math.cos(angle), 12 + 10.5 * math.sin(angle)),
        )
    painter.end()
    return QIcon(pixmap)


def _moon(color: QColor) -> QIcon:
    pixmap, painter = _canvas()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(QRectF(3, 3, 18, 18))
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    painter.drawEllipse(QRectF(8, 0, 18, 18))
    painter.end()
    return QIcon(pixmap)
