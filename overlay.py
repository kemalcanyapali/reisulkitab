"""Focus-free recording HUD, with Hex's compact dot scope for dictation."""

import math
import sys

from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui import QColor, QCursor, QFont, QPainter, QPainterPath, QPen, QFontMetrics
from PyQt6.QtWidgets import QWidget, QApplication

from i18n import t

BARS = 22          # columns of dots in the scope; one per level handed over
ROWS = 9           # rows of dots the braided traces light
HEIGHT = 48        # the plate; the widget is larger by the shadow's PAD
MIN_WIDTH = 210
MAX_WIDTH = 460
MARGIN = 28
GAP = 10        # between two indicators sharing a corner
PAD = 5         # room around the plate for its shadow
SCOPE_WIDTH = 112
WORK_WIDTH = 76
SCOPE_HEIGHT = 26
SCOPE_COLUMNS = 43
SCOPE_PITCH = 2.6

BG = QColor(17, 17, 20, 240)        # Hex's plate: near-black at 0.94
BORDER = QColor(255, 255, 255, 28)  # Hex's rim: white at a tenth
TEXT = QColor(235, 237, 242)
MUTED = QColor(160, 163, 172)

# Hex's Dot scope palette, dark appearance: warm pearl for your voice,
# agent blue while the agent works, coral on failure. Each entry is the pair
# the dots blend, (base, core).
PEARL = (QColor(219, 212, 196), QColor(250, 245, 235))
AGENT = (QColor(179, 199, 230), QColor(235, 242, 255))
CORAL = (QColor(255, 97, 79), QColor(255, 168, 153))
WARN = QColor(240, 180, 80)

STATE_COLORS = {"recording": PEARL, "asking": AGENT, "meeting": PEARL,
                "busy": AGENT, "done": PEARL, "warning": (WARN, WARN),
                "error": CORAL}
LIVE = ("recording", "asking", "meeting")

HELIX = 1.6


def _smoothstep(edge0, edge1, x):
    t = min(1.0, max(0.0, (x - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


class Overlay(QWidget):
    """One indicator. Give it `below` and it stacks on top of that one instead
    of covering it, which is what lets a dictation and a command to the agent be
    under way at the same time and still both be visible."""

    def __init__(self, corner="bottom-center", below=None, dismissable=False, size=150):
        super().__init__(None)
        self.corner = corner
        self.size_percent = size
        self.below = below
        # A job that can run for ten minutes should not have to be watched for
        # ten minutes. Clicking such an indicator puts the progress away; the
        # work carries on and its result still shows up.
        self.dismissable = dismissable
        self.muted = False
        self._stacked = False
        self.state = "idle"
        self.message = ""
        self.levels = [0.0] * BARS
        self.levels2 = [0.0] * BARS   # the other side, while a meeting records
        self.seconds = 0.0
        self._phase = 0.0
        self._concealed = True

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if sys.platform.startswith("linux"):
            # Linux window managers otherwise move this out of the corner.
            flags |= Qt.WindowType.X11BypassWindowManagerHint
        # One that can be clicked away has to receive the click, which means it
        # also swallows one aimed at whatever is underneath it. The rest stay
        # transparent to the mouse, as an indicator should be. It has to be this
        # flag and not WA_TransparentForMouseEvents: on a top-level window the
        # attribute only makes Qt drop the event it already took, so the click
        # never reaches the window below. The flag is the one that tells the
        # display server the window has no input region at all. It is read when
        # the window is created and cannot be turned off later without the
        # window being torn down and built again, which is why the dismissable
        # one has to shrink itself instead (see _conceal).
        if dismissable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            flags |= Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.resize(MIN_WIDTH + 2 * PAD, HEIGHT + 2 * PAD)

        self._anim = QTimer(self)
        self._anim.setInterval(33)
        self._anim.timeout.connect(self._tick)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._conceal)

    # ---- public API --------------------------------------------------

    def show_recording(self, asking=False):
        """The same scope either way, in a different colour when what is being
        recorded is a command for Claude rather than something to paste."""
        self.state = "asking" if asking else "recording"
        self.message = ""
        self.seconds = 0.0
        self.levels = [0.0] * BARS
        self.muted = False   # a new run starts visible, whatever the last one did
        self._hide_timer.stop()
        self._appear()

    def show_meeting(self):
        """Both channels at once: your voice up, the other side down."""
        self.state = "meeting"
        self.message = ""
        self.seconds = 0.0
        self.levels = [0.0] * BARS
        self.levels2 = [0.0] * BARS
        self._hide_timer.stop()
        self._appear()

    def show_busy(self, message):
        # Muted: the stages keep arriving and are simply not drawn. The state is
        # left alone as well, so nothing repaints the box back onto the screen.
        if self.muted:
            self.message = message
            return
        self.state = "busy"
        self.message = message
        self._hide_timer.stop()
        self._appear()

    def show_done(self, message="", msec=2000):
        self._finish("done", message, msec)

    def show_warning(self, message, msec=9000):
        """Finished, but something the user should know about went wrong."""
        self._finish("warning", message, msec)

    def show_error(self, message, msec=6000):
        self._finish("error", message, msec)

    def _finish(self, state, message, msec):
        """An outcome always shows, even one that was told to be quiet: waving
        the progress away asks not to be watched, not to be kept in the dark."""
        self.muted = False
        self.state = state
        self.message = message
        self._appear()
        self._hide_timer.start(msec)

    def dismiss(self):
        self._hide_timer.stop()
        self._conceal()

    def mousePressEvent(self, event):
        # Only a job in progress can be waved away. A recording is short and
        # ending it by accident would cost the words; an outcome goes on its own.
        if self.dismissable and self.state == "busy":
            self.muted = True
            self.dismiss()
        event.accept()

    @property
    def showing(self):
        """Mapped and actually painting something. The window stays mapped while
        idle, so isVisible() alone would always say yes."""
        return self.isVisible() and not self._concealed

    def push_level(self, level):
        self.levels = self.levels[1:] + [level]

    def push_levels(self, mine, theirs):
        self.levels = self.levels[1:] + [mine]
        self.levels2 = self.levels2[1:] + [theirs]

    def set_seconds(self, seconds):
        self.seconds = seconds

    # ---- internals -----------------------------------------------------

    def _appear(self):
        self.setAccessibleName(self.message or (
            t("Recording") if self.state in LIVE else self.state))
        self._resize_to_content()
        self._reposition()
        if not self.isVisible():
            self.show()
        if self._concealed:
            self.raise_()
            self._concealed = False
        if not self._anim.isActive():
            self._anim.start()

    def _conceal(self):
        """Empty the window out instead of unmapping it.

        Unmapping tears the window down and the next dictation builds a new one,
        which makes the compositor repaint whatever sits underneath: on a tiled
        desktop the terminal behind visibly flinches every time the indicator
        goes away. So the window stays mapped and simply paints nothing. It has
        to be a real repaint, not just zero opacity: with the animation stopped
        nothing else damages the surface, and the stale frame would sit on the
        screen until some other event made the compositor redraw it.

        A window that stays mapped also stays clickable, though, and the one
        that can be dismissed is the one that takes clicks. Left at full size it
        would turn its corner of the screen into a dead zone long after there
        was anything to see there, so it shrinks to a point. Resizing keeps the
        surface alive, unlike hiding it.
        """
        self._anim.stop()
        self.state = "hidden"
        self._concealed = True
        self.repaint()
        if self.dismissable:
            self.resize(1, 1)

    @property
    def _compact(self):
        return (self.state in ("recording", "asking")
                or (self.state == "busy" and not self.dismissable))

    def _resize_to_content(self):
        if self._compact:
            width = WORK_WIDTH if self.state == "busy" else SCOPE_WIDTH
            scale = self.size_percent / 100
            self.resize(round((width + 2 * PAD) * scale),
                        round((SCOPE_HEIGHT + 2 * PAD) * scale))
            return
        if self.state in LIVE:
            # A meeting runs long enough to need an hours field.
            width = MIN_WIDTH + (24 if self.state == "meeting" else 0)
        else:
            metrics = QFontMetrics(self._label_font())
            extra = 76 + (18 if self._can_dismiss else 0)
            width = max(MIN_WIDTH,
                        min(MAX_WIDTH, metrics.horizontalAdvance(self.message) + extra))
        self.resize(width + 2 * PAD, HEIGHT + 2 * PAD)

    def _reposition(self):
        # On a multi-monitor setup, show up where the user actually is.
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        left = "left" in self.corner
        top = "top" in self.corner
        self._stacked = self.below is not None and self.below.showing
        # Stack away from the edge the corner sits on, so the pair grows into the
        # screen rather than off it.
        step = (self.below.height() + GAP) if self._stacked else 0
        x = area.left() + MARGIN if left else area.right() - self.width() - MARGIN
        if "center" in self.corner:
            x = area.left() + (area.width() - self.width()) / 2
        y = (area.top() + MARGIN + step if top
             else area.bottom() - self.height() - MARGIN - step)
        self.move(int(x), int(y))

    def _tick(self):
        self._phase += 0.12
        # The one underneath can come and go while this one is up; drop back to
        # the corner when it does rather than leaving a gap where it was.
        if self.below is not None and self.below.showing != self._stacked:
            self._reposition()
        if self.state in LIVE:
            # keep the scope moving even through a pause in speech
            self.levels = self.levels[1:] + [self.levels[-1] * 0.72]
        if self.state == "meeting":
            self.levels2 = self.levels2[1:] + [self.levels2[-1] * 0.72]
        self.update()

    def _label_font(self):
        font = QFont(self.font())
        font.setPointSizeF(10.5)
        return font

    # ---- painting --------------------------------------------------

    def paintEvent(self, _event):
        if self.state == "hidden":
            return  # translucent window, nothing drawn means nothing shown

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._compact:
            width = WORK_WIDTH if self.state == "busy" else SCOPE_WIDTH
            painter.setWindow(0, 0, width + 2 * PAD, SCOPE_HEIGHT + 2 * PAD)
        pair = STATE_COLORS.get(self.state, (MUTED, MUTED))
        plate = self._draw_plate(painter)

        if self._compact:
            painter.setClipPath(plate)
            self._draw_scope(painter, pair)
        elif self.state in LIVE:
            self._draw_scope(painter, pair)
            self._draw_time(painter)
        else:
            self._draw_indicator(painter, pair[0])
            self._draw_message(painter)
            if self._can_dismiss:
                self._draw_dismiss(painter)

    def _draw_plate(self, painter):
        """Hex's plate: a near-black pill floating on a soft shadow, rimmed
        with white at a tenth."""
        rect = QRectF(PAD + 0.5, PAD + 0.5, painter.window().width() - 2 * PAD - 1,
                      painter.window().height() - 2 * PAD - 1)
        radius = rect.height() / 2
        shadow = QPainterPath()
        shadow.addRoundedRect(rect.translated(0.0, 2.5), radius, radius)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for width_, alpha in ((7.0, 26), (4.0, 36), (1.5, 52)):
            painter.setPen(QPen(QColor(0, 0, 0, alpha), width_))
            painter.drawPath(shadow)
        plate = QPainterPath()
        plate.addRoundedRect(rect, radius, radius)
        painter.fillPath(plate, BG)
        painter.setPen(QPen(BORDER, 1))
        painter.drawPath(plate)
        return plate

    def _scope_span(self):
        """Where the dot field runs, leaving the clock its side. A meeting's
        clock carries an hours field, so the field stops earlier."""
        if self._compact:
            width = (WORK_WIDTH if self.state == "busy" else SCOPE_WIDTH) + 2 * PAD
            span = min((SCOPE_COLUMNS - 1) * SCOPE_PITCH, width - 2 * PAD - 4)
            return (width - span) / 2, (width + span) / 2
        right = self.width() - PAD - (80.0 if self.state == "meeting" else 64.0)
        return PAD + 15.0, right

    def _draw_scope(self, painter, pair):
        """The mic, as Hex's Dot scope: a field of dots lit by a braided pair
        of traces that follow the voice and keep turning through a pause.
        There is no separate status dot; the field moving is the statement
        that the microphone is live."""
        if self.state == "meeting":
            self._draw_dual_scope(painter)
            return
        half = (ROWS - 1) * SCOPE_PITCH / 2
        self._braid_dots(painter, self.levels, pair,
                         painter.window().height() / 2 - half,
                         painter.window().height() / 2 + half,
                         self._phase * 1.1, ROWS)

    def _draw_dual_scope(self, painter):
        """Your microphone above the line, what the speakers play below it.

        Seeing both move is the whole check that a meeting is being captured
        properly: one silent half means that side is not reaching the
        recording. Yours is the pearl voice, theirs the agent's blue.
        """
        mid = self.height() / 2
        halves = ((self.levels, PEARL, 0.0, PAD + 4.0, mid - 1.5),
                  (self.levels2, AGENT, math.pi / 2, mid + 1.5,
                   self.height() - PAD - 4.0))
        for levels, pair, turn, top, bottom in halves:
            self._braid_dots(painter, levels, pair, top, bottom,
                             self._phase * 1.1 + turn, 5)

    def _braid_dots(self, painter, levels, pair, top, bottom, phase, rows):
        """One field: every column carries its level, two sine traces out of
        phase braid across them, and each dot lights by how near a trace
        passes. Older columns sit dimmer, the phosphor tail."""
        left, right = self._scope_span()
        mid = (top + bottom) / 2
        pitch = (bottom - top) / (rows - 1)
        painter.setPen(Qt.PenStyle.NoPen)
        base, core = pair
        columns = SCOPE_COLUMNS if self._compact else BARS
        for i in range(columns):
            position = i / (columns - 1)
            level = levels[-1 - abs(i - columns // 2)] if self._compact else levels[i]
            shaped = min(1.0, level ** 0.55)
            x = left + (right - left) * position
            if self.state == "busy":
                head = (self._phase * 0.35) % 1.7 - 0.35
                shaped = 0.16 + 0.84 * math.exp(-((position - head) / 0.18) ** 2)
            amp = (0.10 + 0.90 * shaped) * (bottom - top) / 2
            turn = math.tau * HELIX * position - phase
            y1 = mid + amp * math.sin(turn)
            y2 = mid + amp * 0.92 * math.sin(turn + math.pi * 0.85)
            fresh = (1.0 - 0.55 * abs(2 * position - 1)
                     if self._compact else 0.45 + 0.55 * position)
            for j in range(rows):
                cy = top + pitch * j
                near = min(abs(cy - y1), abs(cy - y2))
                power = math.exp(-((near / 1.15) ** 2)) * shaped * fresh
                edge = abs(cy - mid) / ((bottom - top) / 2)
                self._dot(painter, x, cy, power, pitch, base, core, edge)

    @staticmethod
    def _dot(painter, x, y, power, pitch, base, core, edge):
        """One dot as Hex draws it: a faint floor of unlit grid, brightness
        on a power curve, a hot core colour, and a halo on the brightest."""
        floor = 0.10 * (1.0 - 0.7 * _smoothstep(0.35, 0.95, edge))
        alpha = floor + (0.96 - floor) * power ** 0.85
        heat = power * power
        colour = QColor(int(base.red() + (core.red() - base.red()) * heat),
                        int(base.green() + (core.green() - base.green()) * heat),
                        int(base.blue() + (core.blue() - base.blue()) * heat))
        radius = pitch * (0.25 + 0.17 * power ** 0.6)
        if power > 0.5:
            halo = QColor(colour)
            halo.setAlphaF(0.13 * power * power)
            painter.setBrush(halo)
            painter.drawEllipse(QPointF(x, y), radius * 2.0, radius * 2.0)
        colour.setAlphaF(min(1.0, alpha))
        painter.setBrush(colour)
        painter.drawEllipse(QPointF(x, y), radius, radius)

    def _draw_indicator(self, painter, colour):
        cx, cy = 26.0, self.height() / 2
        if self.state == "busy":
            self._draw_flow(painter, cx, cy)
            return
        painter.setPen(Qt.PenStyle.NoPen)
        if self.state == "done":
            pen = QPen(colour, 2.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPolyline(
                QPointF(cx - 7, cy), QPointF(cx - 2, cy + 5.5), QPointF(cx + 7.5, cy - 6)
            )
        elif self.state == "warning":
            pen = QPen(colour, 2.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(cx, cy - 7), QPointF(cx, cy + 1.5))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawEllipse(QPointF(cx, cy + 6), 1.5, 1.5)
        else:  # error
            pen = QPen(colour, 2.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(cx - 6, cy - 6), QPointF(cx + 6, cy + 6))
            painter.drawLine(QPointF(cx + 6, cy - 6), QPointF(cx - 6, cy + 6))

    def _draw_flow(self, painter, cx, cy):
        """Busy, as Hex's Flow: the resting dots stay put and light travels
        left to right through them while the agent works."""
        painter.setPen(Qt.PenStyle.NoPen)
        head = (self._phase * 2.2) % 11.0 - 5.5
        base, core = AGENT
        for k in range(5):
            x = cx - 11.0 + k * 5.5
            power = 0.12 + 0.88 * math.exp(-(((x - cx - head) / 3.2) ** 2))
            self._dot(painter, x, cy, power, 4.2, base, core, 0.0)

    def _draw_time(self, painter):
        font = QFont(self.font())
        font.setPointSizeF(10.0)
        font.setFamilies(["monospace"])
        painter.setFont(font)
        painter.setPen(MUTED)
        mins, secs = divmod(int(self.seconds), 60)
        hours, mins = divmod(mins, 60)
        text = f"{hours}:{mins:02d}:{secs:02d}" if hours else f"{mins}:{secs:02d}"
        width = 62 if hours else 44
        painter.drawText(
            QRectF(self.width() - width - 12, 0, width, self.height()),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
            text,
        )

    @property
    def _can_dismiss(self):
        return self.dismissable and self.state == "busy"

    def _draw_dismiss(self, painter):
        """A faint cross on the right: without it there is nothing to say the
        box can be clicked away, and a feature nobody can see is not one."""
        cx, cy = self.width() - 18.0, self.height() / 2
        pen = QPen(QColor(MUTED), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QPointF(cx - 4, cy - 4), QPointF(cx + 4, cy + 4))
        painter.drawLine(QPointF(cx + 4, cy - 4), QPointF(cx - 4, cy + 4))

    def _draw_message(self, painter):
        painter.setFont(self._label_font())
        painter.setPen({"error": CORAL[0], "warning": WARN}.get(self.state, TEXT))
        # Leave the cross its corner rather than running the text under it.
        box = QRectF(46, 0, self.width() - 60 - (18 if self._can_dismiss else 0),
                     self.height())
        metrics = QFontMetrics(self._label_font())
        text = metrics.elidedText(self.message, Qt.TextElideMode.ElideRight, int(box.width()))
        painter.drawText(
            box, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text
        )
