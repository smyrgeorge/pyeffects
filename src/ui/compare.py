"""
Before/after comparison widget with a draggable divider.

The original image is drawn across the whole view; the processed image is drawn
on top, clipped to the right of a vertical divider. Dragging the divider (or
clicking anywhere on the image) reveals more or less of the processed result.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QPainter, QPen,
                           QPixmap)
from PySide6.QtWidgets import QWidget

_HANDLE_RADIUS = 11
_HANDLE_HIT = 24  # px around the divider that counts as grabbing the handle


class CompareView(QWidget):
    """Shows two images with a slider that wipes between them."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._before: QPixmap | None = None
        self._after: QPixmap | None = None
        self._split = 0.5          # divider position, 0..1 of the image rect
        self._dragging = False
        self.setMinimumSize(480, 360)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    # -- public API -------------------------------------------------------- #

    def set_before(self, pixmap: QPixmap | None) -> None:
        self._before = pixmap
        self.update()

    def set_after(self, pixmap: QPixmap | None) -> None:
        self._after = pixmap
        self.update()

    def clear(self) -> None:
        self._before = None
        self._after = None
        self.update()

    def has_images(self) -> bool:
        return self._before is not None and self._after is not None

    # -- geometry ---------------------------------------------------------- #

    def _image_rect(self) -> QRectF:
        """The rect the image occupies, scaled to fit and centered."""
        pix = self._before or self._after
        if pix is None or pix.isNull():
            return QRectF(self.rect())

        area = self.rect()
        scale = min(area.width() / pix.width(), area.height() / pix.height())
        w = pix.width() * scale
        h = pix.height() * scale
        x = area.x() + (area.width() - w) / 2
        y = area.y() + (area.height() - h) / 2
        return QRectF(x, y, w, h)

    def _split_x(self, rect: QRectF) -> float:
        return rect.left() + self._split * rect.width()

    # -- painting ---------------------------------------------------------- #

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(24, 24, 28))

        if not self.has_images():
            self._paint_placeholder(painter)
            painter.end()
            return

        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = self._image_rect()
        split_x = self._split_x(rect)

        # Original across the whole image rect.
        painter.drawPixmap(rect, self._before, QRectF(self._before.rect()))

        # Processed image clipped to the right of the divider.
        painter.save()
        clip = QRectF(split_x, rect.top(), rect.right() - split_x, rect.height())
        painter.setClipRect(clip)
        painter.drawPixmap(rect, self._after, QRectF(self._after.rect()))
        painter.restore()

        self._paint_labels(painter, rect, split_x)
        self._paint_divider(painter, rect, split_x)
        painter.end()

    def _paint_placeholder(self, painter: QPainter) -> None:
        painter.setPen(QColor(150, 150, 160))
        font = QFont()
        font.setPointSize(13)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                         "Open an image to begin\n(File ▸ Open…)")

    def _paint_labels(self, painter: QPainter, rect: QRectF, split_x: float) -> None:
        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetrics(font)
        pad = 6

        def badge(text: str, right_align: bool) -> None:
            tw = metrics.horizontalAdvance(text)
            th = metrics.height()
            bw, bh = tw + 2 * pad, th + pad
            if right_align:
                x = rect.right() - bw - 8
            else:
                x = rect.left() + 8
            y = rect.top() + 8
            box = QRectF(x, y, bw, bh)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 140))
            painter.drawRoundedRect(box, 4, 4)
            painter.setPen(QColor(235, 235, 240))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

        # Only show a label if there is room for it on that side.
        if split_x - rect.left() > 70:
            badge("BEFORE", right_align=False)
        if rect.right() - split_x > 70:
            badge("AFTER", right_align=True)

    def _paint_divider(self, painter: QPainter, rect: QRectF, split_x: float) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        pen = QPen(QColor(255, 255, 255, 230))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawLine(QPointF(split_x, rect.top()), QPointF(split_x, rect.bottom()))

        center = QPointF(split_x, rect.center().y())
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.setBrush(QColor(33, 150, 243))
        painter.drawEllipse(center, _HANDLE_RADIUS, _HANDLE_RADIUS)

        # Little left/right chevrons inside the handle.
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        arrow = _HANDLE_RADIUS * 0.45
        painter.drawLine(QPointF(split_x - arrow, center.y()),
                         QPointF(split_x - arrow * 0.3, center.y() - arrow * 0.6))
        painter.drawLine(QPointF(split_x - arrow, center.y()),
                         QPointF(split_x - arrow * 0.3, center.y() + arrow * 0.6))
        painter.drawLine(QPointF(split_x + arrow, center.y()),
                         QPointF(split_x + arrow * 0.3, center.y() - arrow * 0.6))
        painter.drawLine(QPointF(split_x + arrow, center.y()),
                         QPointF(split_x + arrow * 0.3, center.y() + arrow * 0.6))

    # -- interaction ------------------------------------------------------- #

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self.has_images():
            return
        self._dragging = True
        self._set_split_from_x(event.position().x())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self.has_images():
            return
        rect = self._image_rect()
        near = abs(event.position().x() - self._split_x(rect)) <= _HANDLE_HIT
        self.setCursor(Qt.CursorShape.SplitHCursor if (near or self._dragging)
                       else Qt.CursorShape.ArrowCursor)
        if self._dragging:
            self._set_split_from_x(event.position().x())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._dragging = False

    def _set_split_from_x(self, x: float) -> None:
        rect = self._image_rect()
        if rect.width() <= 0:
            return
        frac = (x - rect.left()) / rect.width()
        self._split = max(0.0, min(1.0, frac))
        self.update()
