"""The Hex-like visual shell of the settings window.

Only the shell is drawn here: the navigation rail, the content card, the
grouped cards inside it, buttons, and the message boxes that inherit the
window. Input controls stay native, because the reference (anomalyco/hex)
shows polished platform controls, and because a native combobox or spinbox
is what the rest of the desktop offers.

Colors are picked from the live Qt palette, never from a setting: when the
system switches between light and dark, Qt moves the palette, the change
arrives here as an ApplicationPaletteChange event, and the sheet is rebuilt
from the new tokens.

THESIS: a quiet two-pane settings shell in the manner of Hex — a rail of
page names on a flat field, one rounded content card, hairline borders,
system type, no shadows.
OWN-WORLD: depth by border and inset, never by shadow; selection is a soft
pill; one neutral text scale from primary to muted; color is spent nowhere
else.
STORY: settings read top-to-bottom like a document; the rail says where
you are, the card holds one stack of sections at a time.
FORM (seed 003a3be4): rail at most 184px wide and shrinking with the
window, rows ~32px with 16px stroke icons; card inset 9px; content margins
18/12; card radius 12, grouped cards 10, buttons 7; system font, heading
14pt/600.
"""

from string import Template

from PyQt6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap
from PyQt6.QtWidgets import QApplication

# Neutral scales measured from the Hex settings goldens, one per scheme.
LIGHT = {
    "win": "#f4f5f6",        # the field behind everything
    "card": "#ffffff",       # content card and grouped cards
    "inset": "#f7f8f9",      # a group nested inside a card
    "border": "#e4e6e9",     # card edges
    "border_soft": "#eceef0",  # edges of nested groups
    "text": "#191b1f",
    "text2": "#454b54",
    "muted": "#71767f",
    "nav_sel": "#e8e9ec",    # the pill behind the open page
    "hover": "#ebecef",
    "btn": "#ffffff",
    "btn_hover": "#f2f3f5",
    "btn_pressed": "#e8e9ec",
    "btn_border": "#d9dce0",
    "btn_disabled": "#f4f5f6",
    "focus": "#c2c6cd",
}
DARK = {
    "win": "#0d0e10",
    "card": "#17181b",
    "inset": "#1d1e22",
    "border": "#27282d",
    "border_soft": "#222327",
    "text": "#ececee",
    "text2": "#b4b7bd",
    "muted": "#7d818a",
    "nav_sel": "#26272c",
    "hover": "#1d1e22",
    "btn": "#1f2024",
    "btn_hover": "#26272c",
    "btn_pressed": "#2c2d33",
    "btn_border": "#34353b",
    "btn_disabled": "#1a1b1e",
    "focus": "#4a4c55",
}

_SHEET = Template("""
#settingsRoot { background: $win; }

#settingsNav {
    background: transparent;
    border: none;
    outline: 0;
    padding: 8px 6px 8px 10px;
}
/* One transparent border everywhere, so drawing the focus ring does not
   move the row it arrives on. */
#settingsNav::item {
    color: $text2;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    /* The style pads items on its own; a cap keeps rows Hex-tight. */
    max-height: 24px;
    padding: 4px 10px;
    margin: 0 2px;
}
#settingsNav::item:hover { background: $hover; color: $text; }
#settingsNav::item:selected { background: $nav_sel; color: $text; }
#settingsNav::item:focus { border-color: $focus; }

#settingsHeading {
    background: transparent;
    color: $text;
    font-size: 14pt;
    font-weight: 600;
}

#settingsContent {
    background: $card;
    border: 1px solid $border;
    border-radius: 12px;
}

/* The pages sit straight on the card; the scroll areas must not paint. */
#settingsContent QTabWidget, #settingsContent QTabWidget::pane,
#settingsContent QScrollArea, #settingsContent QScrollArea > QWidget > QWidget,
#settingsContent QTabWidget > QWidget, #settingsContent QDialogButtonBox {
    background: transparent;
    border: none;
}

/* The one tab bar that stays visible: Dictation / Audio file. */
QTabBar { background: transparent; }
QTabBar::tab {
    color: $text2;
    background: transparent;
    padding: 5px 12px;
    margin: 2px 2px 4px 2px;
    border-radius: 6px;
}
QTabBar::tab:hover { color: $text; }
QTabBar::tab:selected { color: $text; background: $nav_sel; }

QGroupBox {
    background: $card;
    border: 1px solid $border;
    border-radius: 10px;
    margin-top: 12px;
    padding: 2px 14px 10px 14px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding-right: 4px;
    color: $text2;
    font-weight: 600;
}

/* A model box inside a card is an inset, not a card on a card. */
LocalModelBox {
    background: $inset;
    border: 1px solid $border_soft;
    border-radius: 8px;
}

QPushButton {
    background: $btn;
    border: 1px solid $btn_border;
    border-radius: 7px;
    padding: 4px 14px;
    color: $text;
}
QPushButton:hover { background: $btn_hover; }
QPushButton:pressed { background: $btn_pressed; }
QPushButton:focus { border-color: $focus; }
QPushButton:disabled {
    color: $muted;
    background: $btn_disabled;
    border-color: $border;
}

/* Message boxes inherit the sheet; keep them on the card, not half native. */
QMessageBox { background: $card; }
QMessageBox QLabel { background: transparent; color: $text; }
""")

def _tokens(palette):
    window = palette.color(QPalette.ColorRole.Window)
    return DARK if window.lightness() < 128 else LIGHT


def _restyle(window):
    # The application palette, not the window's own: a styled window's
    # palette is overwritten by the sheet, so reading it back would only
    # ever confirm the scheme already chosen.
    palette = QApplication.instance().palette()
    window.setStyleSheet(_SHEET.substitute(_tokens(palette)))


class _PaletteWatch(QObject):
    """Rebuilds the sheet when the system palette (light/dark) moves."""

    def eventFilter(self, watched, event):
        if event.type() is QEvent.Type.ApplicationPaletteChange:
            _restyle(watched)
        return False


# ---- rail icons ---------------------------------------------------------

def _sliders(p):
    for x in (4.4, 8, 11.6):
        p.drawLine(QPointF(x, 2.6), QPointF(x, 13.4))
    for x, y in ((4.4, 10.2), (8, 5.6), (11.6, 8.2)):
        p.drawLine(QPointF(x - 1.7, y), QPointF(x + 1.7, y))


def _chip(p):
    p.drawRoundedRect(QRectF(4, 4, 8, 8), 2, 2)
    p.drawRoundedRect(QRectF(6.8, 6.8, 2.4, 2.4), 1, 1)
    for x in (6, 10):
        p.drawLine(QPointF(x, 2), QPointF(x, 4))
        p.drawLine(QPointF(x, 12), QPointF(x, 14))
    for y in (6, 10):
        p.drawLine(QPointF(2, y), QPointF(4, y))
        p.drawLine(QPointF(12, y), QPointF(14, y))


def _rules(p):
    for y in (4.2, 8, 11.8):
        p.drawLine(QPointF(2.4, y), QPointF(9.4, y))
    p.drawLine(QPointF(10.6, 11.2), QPointF(12.2, 12.8))
    p.drawLine(QPointF(12.2, 12.8), QPointF(14.8, 9.0))


def _agent(p):
    p.drawRoundedRect(QRectF(2, 3, 12, 8.4), 3.4, 3.4)
    p.drawLine(QPointF(5.4, 11.4), QPointF(5.4, 14.4))
    p.drawLine(QPointF(5.4, 14.4), QPointF(8.8, 11.4))


def _meeting(p):
    # A calendar: two people do not survive 16px, a date block does.
    p.drawRoundedRect(QRectF(2.2, 3.4, 11.6, 10.6), 1.8, 1.8)
    for x in (5.4, 10.6):
        p.drawLine(QPointF(x, 1.6), QPointF(x, 4.2))
    p.drawLine(QPointF(2.2, 6.8), QPointF(13.8, 6.8))
    # No inner dots: at this size the rings and header carry the calendar.


def _minutes(p):
    p.drawPolyline(QPointF(4, 1.8), QPointF(9.2, 1.8), QPointF(12.6, 5.2),
                   QPointF(12.6, 14.2), QPointF(4, 14.2), QPointF(4, 1.8))
    p.drawPolyline(QPointF(9.2, 1.8), QPointF(9.2, 5.2), QPointF(12.6, 5.2))
    p.drawLine(QPointF(6.4, 8.6), QPointF(10.2, 8.6))


def _file(p):
    for x, half in ((3.2, 2.2), (6.4, 4.4), (9.6, 3.2), (12.8, 2.2)):
        p.drawLine(QPointF(x, 8 - half), QPointF(x, 8 + half))


def _shortcuts(p):
    # One keycap with an enter arrow: rows of keys do not survive 16px.
    p.drawRoundedRect(QRectF(2.6, 2.6, 10.8, 10.8), 2.4, 2.4)
    p.drawPolyline(QPointF(5, 9.6), QPointF(10.4, 9.6), QPointF(10.4, 5.6))


def _history(p):
    p.drawEllipse(QRectF(2.4, 2.4, 11.2, 11.2))
    p.drawLine(QPointF(8, 8), QPointF(8, 4.8))
    p.drawLine(QPointF(8, 8), QPointF(10.4, 9.3))


_NAV_DRAW = {
    "general": _sliders, "models": _chip, "rules": _rules,
    "agent": _agent, "meeting": _meeting, "minutes": _minutes,
    "file": _file, "shortcuts": _shortcuts, "history": _history,
}
# One gray that clears 3:1 against both the light and the dark rail.
ICON_COLOR = "#7a7f89"


def nav_icon(kind):
    """A 16px line icon for one rail row, drawn, not shipped as an asset."""
    pixmap = QPixmap(32, 32)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(ICON_COLOR), 2.25)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    _NAV_DRAW[kind](painter)
    painter.end()
    return QIcon(pixmap)


def apply(window):
    """Name and style the settings shell, and follow the system scheme."""
    window.setObjectName("settingsRoot")
    watch = _PaletteWatch(window)
    window.installEventFilter(watch)
    # Kept as an attribute so the filter lives as long as the window.
    window._palette_watch = watch
    _restyle(window)
