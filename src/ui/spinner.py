"""A small translucent busy spinner overlay (a rotating arc)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget


class Spinner(QWidget):
    """A translucent overlay with a rotating arc, shown while busy."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._angle = 0
        self.setFixedSize(132, 96)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def _tick(self) -> None:
        self._angle = (self._angle + 24) % 360
        self.update()

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
        self.show()
        self.raise_()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 20, 24, 205))
        painter.drawRoundedRect(QRectF(self.rect()), 14, 14)

        diameter = 34
        arc = QRectF((self.width() - diameter) / 2, 16, diameter, diameter)
        ring = QPen(QColor(255, 255, 255, 45), 4)
        ring.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(ring)
        painter.drawArc(arc, 0, 360 * 16)
        moving = QPen(QColor(70, 160, 255), 4)
        moving.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(moving)
        painter.drawArc(arc, -self._angle * 16, 100 * 16)

        painter.setPen(QColor(230, 230, 235))
        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)
        painter.drawText(QRectF(0, 16 + diameter + 8, self.width(), 20),
                         Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                         "Rendering…")
