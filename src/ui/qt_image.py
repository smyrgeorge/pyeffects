"""
Conversion helpers between Pillow images and Qt.
"""

from __future__ import annotations

from PIL import Image
from PySide6.QtGui import QImage, QPixmap


def pil_to_qpixmap(image: Image.Image) -> QPixmap:
    """
    Convert a Pillow image into a :class:`QPixmap`.

    The pixel data is copied so the returned pixmap does not depend on the
    lifetime of any intermediate buffer.
    """
    rgba = image.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qimage = QImage(data, rgba.width, rgba.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimage.copy())
