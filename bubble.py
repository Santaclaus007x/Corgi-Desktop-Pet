"""Speech bubble that floats above the corgi. Clicks pass straight through it."""
from __future__ import annotations

import time

from qt_compat import QtCore, QtGui, QtWidgets, Qt

PAD = 9
TAIL = 9
MAX_W = 240


class SpeechBubble(QtWidgets.QWidget):
    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        if hasattr(Qt.WidgetAttribute, "WA_MacAlwaysShowToolWindow"):
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)

        f = QtGui.QFontDatabase.systemFont(
            QtGui.QFontDatabase.SystemFont.FixedFont)
        f.setPointSize(10)
        f.setBold(True)
        self.setFont(f)

        self.text = ""
        self.until = 0.0
        self.sticky: str | None = None      # e.g. the Pomodoro countdown
        self._showing_sticky = False
        self._tail_x = 0

    # ── public API ─────────────────────────────────────────────────────
    def say(self, text: str, seconds: float = 4.0):
        self.text = text
        self.until = time.monotonic() + seconds
        self._showing_sticky = False
        self._relayout()

    def set_sticky(self, text: str | None):
        changed = text != self.sticky
        self.sticky = text
        if self._showing_sticky and changed:
            self.text = text or ""
            self._relayout()

    def tick(self, pet_geom: QtCore.QRect, head_y: int):
        now = time.monotonic()
        if self.text and not self._showing_sticky and now > self.until:
            self.text = ""
        if not self.text and self.sticky:
            self.text = self.sticky
            self._showing_sticky = True
            self._relayout()
        if not self.text:
            if self.isVisible():
                self.hide()
            return
        self._place(pet_geom, head_y)
        if not self.isVisible():
            self.show()

    # ── internals ──────────────────────────────────────────────────────
    def _relayout(self):
        fm = QtGui.QFontMetrics(self.font())
        r = fm.boundingRect(QtCore.QRect(0, 0, MAX_W, 2000),
                            int(Qt.TextFlag.TextWordWrap), self.text)
        self.resize(r.width() + PAD * 2 + 2, r.height() + PAD * 2 + TAIL + 2)
        self.update()

    def _place(self, pet: QtCore.QRect, head_y: int):
        cx = pet.center().x()
        x = cx - self.width() // 2
        y = head_y - self.height()
        screen = QtGui.QGuiApplication.screenAt(pet.center()) \
            or QtGui.QGuiApplication.primaryScreen()
        g = screen.availableGeometry()
        x = max(g.left() + 2, min(x, g.right() - self.width() - 2))
        if y < g.top():                     # no room above: show below pet
            y = pet.bottom() + 4
        self._tail_x = max(PAD + 4, min(cx - x, self.width() - PAD - 4))
        if self.pos() != QtCore.QPoint(x, y):
            self.move(x, y)

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        body = QtCore.QRectF(1, 1, self.width() - 2, self.height() - TAIL - 2)
        path = QtGui.QPainterPath()
        path.addRoundedRect(body, 8, 8)
        tail = QtGui.QPolygonF([
            QtCore.QPointF(self._tail_x - 6, body.bottom() - 1),
            QtCore.QPointF(self._tail_x + 6, body.bottom() - 1),
            QtCore.QPointF(self._tail_x, body.bottom() + TAIL - 1),
        ])
        tp = QtGui.QPainterPath()
        tp.addPolygon(tail)
        path = path.united(tp)
        p.setPen(QtGui.QPen(QtGui.QColor(58, 34, 22), 2))
        p.setBrush(QtGui.QColor(255, 249, 238))
        p.drawPath(path)
        p.setPen(QtGui.QColor(58, 34, 22))
        p.drawText(body.adjusted(PAD, PAD, -PAD, -PAD),
                   int(Qt.TextFlag.TextWordWrap), self.text)
