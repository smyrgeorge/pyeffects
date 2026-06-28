"""Small widget factories and helpers shared by the main window."""

from __future__ import annotations

import math

from PIL import Image
from PySide6.QtWidgets import QDoubleSpinBox, QFrame, QLabel, QSpinBox, QWidget

from effects.base import Param, ParamKind


def step_decimals(step: float) -> int:
    """Number of decimals implied by a float step (0.01 → 2, 0.1 → 1, 1 → 0)."""
    if step <= 0:
        return 2
    return max(0, min(4, -math.floor(math.log10(step))))


def make_param_spin(param: Param) -> QWidget:
    """A spin box matching a numeric parameter's type, range, and step."""
    if param.kind is ParamKind.INT:
        spin = QSpinBox()
        spin.setRange(int(param.min if param.min is not None else 0),
                      int(param.max if param.max is not None else 100))
        spin.setSingleStep(int(param.step or 1))
    else:
        spin = QDoubleSpinBox()
        step = float(param.step or 0.01)
        spin.setRange(float(param.min if param.min is not None else 0.0),
                      float(param.max if param.max is not None else 1.0))
        spin.setSingleStep(step)
        spin.setDecimals(step_decimals(step))
    spin.setToolTip(param.help)
    return spin


def section_label(text: str) -> QLabel:
    """An uppercase, dimmed section header label."""
    label = QLabel(text.upper())
    label.setStyleSheet("color: #9a9aa6; font-size: 11px; font-weight: 600;"
                        " letter-spacing: 1px;")
    return label


def divider() -> QFrame:
    """A thin horizontal separator line."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setStyleSheet("color: #34343c;")
    return line


def downscale(image: Image.Image, max_dim: int) -> Image.Image:
    """Shrink ``image`` so its longest edge is at most ``max_dim`` (else unchanged)."""
    if max(image.width, image.height) <= max_dim:
        return image
    scale = max_dim / max(image.width, image.height)
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)
