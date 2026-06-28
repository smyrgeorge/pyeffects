"""
Modal dialog shown while a video renders.

Two phases: first a determinate progress bar for frame rendering, then an
indeterminate "busy" bar plus a live, scrolling ffmpeg log while the clip is
encoded. The controller (MainWindow) drives it through signals from the render
worker, and calls :meth:`finish` to close it when the worker is done.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit,
                               QProgressBar, QPushButton, QVBoxLayout, QWidget)


class RenderDialog(QDialog):
    """Frame-render progress, then a live ffmpeg encode log."""

    canceled = Signal()

    def __init__(self, frames: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Render video")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(480)
        self._finished = False

        self._label = QLabel("Rendering video…")

        self._bar = QProgressBar()
        self._bar.setRange(0, max(1, frames))
        self._bar.setValue(0)

        # Hidden until encoding starts, then streams ffmpeg's output.
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(2000)   # bound memory, keep a long tail
        self._log.setMinimumHeight(180)
        self._log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._log.setStyleSheet(
            "QPlainTextEdit { background:#141418; color:#c8c8d0;"
            " border:1px solid #2a2a32; border-radius:6px; padding:6px;"
            " font-family:'SF Mono','Menlo','Consolas',monospace; font-size:11px; }")
        self._log.hide()

        self._cancel = QPushButton("Cancel")
        self._cancel.clicked.connect(self._request_cancel)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self._cancel)

        layout = QVBoxLayout(self)
        layout.addWidget(self._label)
        layout.addWidget(self._bar)
        layout.addWidget(self._log)
        layout.addLayout(buttons)

    # -- driven by the worker's signals (UI thread) ------------------------- #

    def set_frame_progress(self, done: int, total: int) -> None:
        self._bar.setRange(0, max(1, total))
        self._bar.setValue(done)
        self._label.setText(f"Rendering frame {done} / {total}…")

    def start_encoding(self) -> None:
        """All frames rendered; show the busy bar and reveal the log pane."""
        self._bar.setRange(0, 0)               # indeterminate -> animated busy bar
        self._label.setText("Encoding video… (ffmpeg)")
        if self._log.isHidden():
            self._log.show()
            self.adjustSize()

    def append_log(self, line: str) -> None:
        self._log.appendPlainText(line)
        scroll = self._log.verticalScrollBar()
        scroll.setValue(scroll.maximum())      # keep the newest line in view

    def finish(self) -> None:
        """Mark the render complete and close the dialog (called by the controller)."""
        self._finished = True
        self.close()

    # -- cancellation ------------------------------------------------------- #

    def _request_cancel(self) -> None:
        if self._finished:
            return
        self._cancel.setEnabled(False)
        self._label.setText("Cancelling…")
        self.canceled.emit()                   # controller closes us when it stops

    def reject(self) -> None:                  # Esc key
        self._request_cancel()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt naming)
        # The window [x] acts as Cancel; stay open until the worker actually stops.
        if self._finished:
            event.accept()
        else:
            self._request_cancel()
            event.ignore()
