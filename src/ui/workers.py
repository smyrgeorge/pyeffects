"""
Background workers that run image effects off the Qt UI thread.

Both apply the (CPU-heavy, GIL-releasing) effect work in a :class:`QThread` so a
slow render — e.g. *Glitch Height* — never freezes the window. Results are
reported back to the UI thread via signals.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget

from effects.base import Effect
from render.video import render_video


class PreviewWorker(QThread):
    """Applies an effect to the preview image off the UI thread."""

    done = Signal(object)   # the resulting PIL image
    failed = Signal(str)

    def __init__(self, effect: Effect, image: Image.Image, values: dict,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._effect = effect
        self._image = image
        self._values = values

    def run(self) -> None:
        try:
            self.done.emit(self._effect.apply(self._image, **self._values))
        except Exception as exc:  # noqa: BLE001 — report to the UI thread
            self.failed.emit(str(exc))


class VideoWorker(QThread):
    """Renders a parameter-sweep video off the UI thread, reporting progress."""

    progress = Signal(int, int)   # (frames done, frames total)
    done = Signal(object)         # output Path, or None if cancelled
    failed = Signal(str)

    def __init__(self, image: Image.Image, effect: Effect, output: str,
                 source_path: Path, sweeps: dict, values: dict,
                 duration: float, fps: int, frames: int, max_size: int | None,
                 smooth: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image = image
        self._effect = effect
        self._output = output
        self._source_path = source_path
        self._sweeps = sweeps
        self._values = values
        self._duration = duration
        self._fps = fps
        self._frames = frames
        self._max_size = max_size
        self._smooth = smooth
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        try:
            out = render_video(
                self._image, self._effect, self._output,
                source_path=self._source_path, sweeps=self._sweeps,
                values=self._values, duration=self._duration, fps=self._fps,
                frames=self._frames, max_size=self._max_size, smooth=self._smooth,
                on_progress=lambda d, t: self.progress.emit(d, t),
                should_cancel=lambda: self._cancelled)
            self.done.emit(out)
        except Exception as exc:  # noqa: BLE001 — report to the UI thread
            self.failed.emit(str(exc))
