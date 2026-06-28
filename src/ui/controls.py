"""
Dynamic control panel.

Given any :class:`~effects.base.Effect`, this widget reads its ``params()`` and
builds the matching controls (sliders, checkboxes, dropdowns). It emits
:attr:`changed` with the full settings dict whenever the user edits a value, so
the host window can re-render the preview.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout,
                               QLabel, QSlider, QWidget)

from effects.base import Effect, Param, ParamKind

_FLOAT_STEPS = 1000  # slider resolution for float parameters


class ControlPanel(QWidget):
    """Builds and manages the controls for a single effect."""

    changed = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._effect: Effect | None = None
        self._getters: dict[str, Callable[[], Any]] = {}
        self._resetters: list[Callable[[], None]] = []

        # The panel is just a compact form; its header/Reset live in the sidebar
        # section header (matching the Render video / Transitions panels).
        self._form = QFormLayout(self)
        self._form.setContentsMargins(0, 0, 0, 0)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._form.setHorizontalSpacing(12)
        self._form.setVerticalSpacing(8)

    # -- public API -------------------------------------------------------- #

    def set_effect(self, effect: Effect) -> None:
        """Rebuild the panel for ``effect``."""
        self._effect = effect
        self._getters.clear()
        self._resetters.clear()
        _clear_form(self._form)

        for param in effect.params():
            label, field = self._build_control(param)
            self._form.addRow(label, field)

    def values(self) -> dict[str, Any]:
        """Return the current value of every control."""
        return {name: get() for name, get in self._getters.items()}

    def reset(self) -> None:
        """Restore every control to its parameter default and re-render once."""
        for resetter in self._resetters:
            resetter()
        self._emit()

    # -- control construction --------------------------------------------- #

    def _build_control(self, param: Param) -> tuple[str, QWidget]:
        if param.kind is ParamKind.BOOL:
            return param.label, self._build_bool(param)
        if param.kind is ParamKind.CHOICE:
            return param.label, self._build_choice(param)
        return param.label, self._build_slider(param)

    def _build_bool(self, param: Param) -> QWidget:
        box = QCheckBox()
        box.setChecked(bool(param.default))
        box.setToolTip(param.help)
        box.toggled.connect(self._emit)
        self._getters[param.name] = box.isChecked
        self._resetters.append(lambda b=box, d=param.default: b.setChecked(bool(d)))
        return box

    def _build_choice(self, param: Param) -> QWidget:
        combo = QComboBox()
        for choice in param.choices:
            combo.addItem(str(choice), choice)
        idx = combo.findData(param.default)
        combo.setCurrentIndex(max(0, idx))
        combo.setToolTip(param.help)
        combo.currentIndexChanged.connect(self._emit)
        self._getters[param.name] = combo.currentData
        self._resetters.append(
            lambda c=combo, d=param.default: c.setCurrentIndex(max(0, c.findData(d))))
        return combo

    def _build_slider(self, param: Param) -> QWidget:
        is_int = param.kind is ParamKind.INT
        lo = float(param.min if param.min is not None else 0.0)
        hi = float(param.max if param.max is not None else 1.0)

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setToolTip(param.help)
        value_label = QLabel()
        value_label.setMinimumWidth(48)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value_label.setStyleSheet("color: #2196f3;")

        if is_int:
            slider.setRange(int(lo), int(hi))
            slider.setValue(int(param.default))
            slider.setSingleStep(int(param.step or 1))

            def get() -> int:
                return slider.value()

            def show(_: int) -> None:
                value_label.setText(str(slider.value()))
        else:
            slider.setRange(0, _FLOAT_STEPS)
            slider.setValue(_to_slider(float(param.default), lo, hi))

            def get() -> float:
                return _from_slider(slider.value(), lo, hi)

            def show(_: int) -> None:
                value_label.setText(f"{get():.2f}")

        show(0)
        slider.valueChanged.connect(show)
        slider.valueChanged.connect(self._emit)
        self._getters[param.name] = get

        default_pos = int(param.default) if is_int else _to_slider(float(param.default), lo, hi)
        self._resetters.append(lambda s=slider, p=default_pos: s.setValue(p))

        row = QWidget()
        box = QHBoxLayout(row)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(slider, 1)
        box.addWidget(value_label)
        return row

    # -- signals ----------------------------------------------------------- #

    def _emit(self, *_: Any) -> None:
        self.changed.emit(self.values())


def _to_slider(value: float, lo: float, hi: float) -> int:
    if hi <= lo:
        return 0
    return round((value - lo) / (hi - lo) * _FLOAT_STEPS)


def _from_slider(pos: int, lo: float, hi: float) -> float:
    return lo + (pos / _FLOAT_STEPS) * (hi - lo)


def _clear_form(form: QFormLayout) -> None:
    while form.rowCount():
        form.removeRow(0)
