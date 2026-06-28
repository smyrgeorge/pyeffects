"""
The main application window.

A reusable window for previewing image effects: a before/after slider on the
left and, on the right, a sidebar that builds its effect-settings controls
dynamically and exposes the still/video export. Previews render on a background
thread (with a busy spinner) so a slow effect never freezes the UI.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QFrame, QHBoxLayout,
                               QLabel, QMainWindow, QMessageBox, QProgressDialog,
                               QPushButton, QScrollArea, QSizePolicy, QSpinBox,
                               QVBoxLayout, QWidget)

from effects.base import Effect, ParamKind
from render.video import (DEFAULT_DURATION, DEFAULT_FPS, DEFAULT_FRAMES,
                          DEFAULT_MAX_SIZE)
from ui.compare import CompareView
from ui.controls import ControlPanel
from ui.qt_image import pil_to_qpixmap
from ui.spinner import Spinner
from ui.widgets import divider, downscale, make_param_spin, section_label
from ui.workers import PreviewWorker, VideoWorker

#: Largest dimension (px) used for the live preview. Saving always uses full res.
_PREVIEW_MAX = 1100
#: Default delay (ms) before re-rendering after a control changes, so dragging a
#: slider coalesces into a single render. Override via the ``debounce_ms`` arg.
_DEFAULT_DEBOUNCE_MS = 500

_SUPPORTED = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.webp)"

#: Image auto-loaded on startup if it exists (relative to the project root).
_WORKSPACE = Path(__file__).resolve().parents[2] / "workspace"
_DEFAULT_IMAGE = _WORKSPACE / "flower.jpg"


class MainWindow(QMainWindow):
    def __init__(self, effects: list[Effect], debounce_ms: int | None = None) -> None:
        super().__init__()
        self.setWindowTitle("PyEffects")
        self.resize(1180, 760)

        self._debounce_ms = (_DEFAULT_DEBOUNCE_MS if debounce_ms is None
                             else max(0, int(debounce_ms)))
        self._effects = effects
        self._effect: Effect | None = effects[0] if effects else None
        self._original: Image.Image | None = None   # full-resolution source
        self._preview_src: Image.Image | None = None  # downscaled for preview
        self._source_path: Path | None = None       # path of the loaded image
        self._video_worker: VideoWorker | None = None
        self._video_progress: QProgressDialog | None = None
        self._preview_thread: PreviewWorker | None = None
        self._preview_dirty = False                  # re-render requested mid-render

        self._compare = CompareView()
        self._controls = ControlPanel()
        self._controls.changed.connect(self._on_controls_changed)

        # Busy spinner shown over the preview while an effect renders.
        self._spinner = Spinner(self._compare)
        self._compare.installEventFilter(self)

        # Debounce timer for live preview.
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self._debounce_ms)
        self._timer.timeout.connect(self._render_preview)

        # Show the spinner only if a render takes a moment (avoids flashing).
        self._spinner_delay = QTimer(self)
        self._spinner_delay.setSingleShot(True)
        self._spinner_delay.setInterval(180)
        self._spinner_delay.timeout.connect(self._show_spinner)

        self._build_ui()
        if self._effect is not None:
            self._controls.set_effect(self._effect)
            self._rebuild_transitions(self._effect)

        self._load_default_image()

    # -- layout ------------------------------------------------------------ #

    def _build_ui(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._compare, 1)
        root.addWidget(self._build_sidebar())
        self.setCentralWidget(central)

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setFixedWidth(330)
        sidebar.setStyleSheet("QFrame { background: #1f1f25; color: #e6e6ea; }")

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Open button.
        self._open_btn = QPushButton("Open…")
        self._open_btn.clicked.connect(self.open_image)
        layout.addWidget(self._open_btn)

        # Effect selector.
        layout.addWidget(section_label("Effect"))
        self._effect_combo = QComboBox()
        for effect in self._effects:
            self._effect_combo.addItem(effect.name, effect)
        self._effect_combo.currentIndexChanged.connect(self._on_effect_changed)
        layout.addWidget(self._effect_combo)

        layout.addWidget(divider())

        # All settings (effect + render) live in one scroll so the action
        # buttons below stay pinned no matter how many parameters there are.
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)

        body.addWidget(self._section_header("Effect settings", self._controls.reset))
        body.addWidget(self._controls)

        body.addWidget(divider())
        body.addWidget(self._section_header("Render video", self._reset_video_settings))
        body.addLayout(self._build_video_panel())

        body.addWidget(self._section_header("Transitions (from → to)", self._reset_transitions))
        self._trans_form = QFormLayout()
        self._trans_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self._trans_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._trans_form.setHorizontalSpacing(8)
        self._trans_form.setVerticalSpacing(8)
        body.addLayout(self._trans_form)
        body.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        # Pinned actions.
        layout.addWidget(divider())
        self._save_btn = QPushButton("Save image…")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self.save_image)
        layout.addWidget(self._save_btn)

        self._video_btn = QPushButton("Render video…")
        self._video_btn.setToolTip("Render a video animating the effect parameters over time")
        self._video_btn.setEnabled(False)
        self._video_btn.clicked.connect(self.export_video)
        layout.addWidget(self._video_btn)

        self._status = QLabel("Open an image to begin.")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: #9a9aa6; font-size: 11px;")
        layout.addWidget(self._status)

        return sidebar

    def _build_video_panel(self) -> QFormLayout:
        """Global video render settings (duration, fps, frames, size, smoothing)."""
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        # Grow every field to the same width so the inputs (and their spin
        # up/down buttons) line up.
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self._dur_spin = QDoubleSpinBox()
        self._dur_spin.setRange(1.0, 600.0)
        self._dur_spin.setDecimals(1)
        self._dur_spin.setSingleStep(0.5)
        self._dur_spin.setSuffix(" s")
        self._dur_spin.setValue(DEFAULT_DURATION)

        self._fps_spin = QSpinBox()
        self._fps_spin.setRange(1, 60)
        self._fps_spin.setSuffix(" fps")
        self._fps_spin.setValue(DEFAULT_FPS)

        self._frames_spin = QSpinBox()
        self._frames_spin.setRange(2, 1000)
        self._frames_spin.setValue(DEFAULT_FRAMES)
        self._frames_spin.setToolTip("Distinct frames rendered across the animation "
                                     "(higher = smoother, slower)")

        # Resolution: render at the input's native size by default; unticking it
        # enables a max-size cap.
        self._native_check = QCheckBox("Native (input size)")
        self._native_check.setToolTip("Render at the input image's full resolution")

        self._size_spin = QSpinBox()
        self._size_spin.setRange(120, 3840)
        self._size_spin.setSingleStep(40)
        self._size_spin.setSuffix(" px")
        self._size_spin.setValue(DEFAULT_MAX_SIZE)
        self._native_check.toggled.connect(lambda on: self._size_spin.setEnabled(not on))
        self._native_check.setChecked(True)   # disables the size spin via the signal

        self._smooth_combo = QComboBox()
        self._smooth_combo.addItem("Blend (smooth)", "blend")
        self._smooth_combo.addItem("Motion", "motion")
        self._smooth_combo.addItem("None", "none")
        self._smooth_combo.setToolTip("How in-between frames are filled to reach the frame rate")

        for field in (self._dur_spin, self._fps_spin, self._frames_spin,
                      self._size_spin, self._smooth_combo):
            field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        form.addRow("Duration", self._dur_spin)
        form.addRow("Frame rate", self._fps_spin)
        form.addRow("Frames", self._frames_spin)
        form.addRow("Resolution", self._native_check)
        form.addRow("Max size", self._size_spin)
        form.addRow("Smoothing", self._smooth_combo)
        return form

    def _section_header(self, text: str, on_reset) -> QWidget:
        """A section label with a subtle right-aligned Reset link."""
        row = QWidget()
        box = QHBoxLayout(row)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(section_label(text))
        box.addStretch(1)
        reset = QPushButton("Reset")
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.setStyleSheet("QPushButton { color: #9a9aa6; border: none; font-size: 11px; }"
                            " QPushButton:hover { color: #e6e6ea; }")
        reset.clicked.connect(on_reset)
        box.addWidget(reset)
        return row

    def _reset_video_settings(self) -> None:
        """Restore the render-video settings to their defaults."""
        self._dur_spin.setValue(DEFAULT_DURATION)
        self._fps_spin.setValue(DEFAULT_FPS)
        self._frames_spin.setValue(DEFAULT_FRAMES)
        self._size_spin.setValue(DEFAULT_MAX_SIZE)
        self._smooth_combo.setCurrentIndex(0)
        self._native_check.setChecked(True)

    def _reset_transitions(self) -> None:
        """Restore the per-variable From→To ranges to their defaults."""
        if self._effect is not None:
            self._rebuild_transitions(self._effect)

    def _rebuild_transitions(self, effect: Effect) -> None:
        """Rebuild the per-variable From→To inputs for the current effect."""
        while self._trans_form.rowCount():
            self._trans_form.removeRow(0)
        self._trans_spins: dict[str, tuple] = {}

        numeric = [p for p in effect.params()
                   if p.kind in (ParamKind.FLOAT, ParamKind.INT)]
        # By default animate one parameter across its full range (prefer
        # "strength") and hold the rest at their defaults.
        primary = next((p.name for p in numeric if p.name == "strength"),
                       numeric[0].name if numeric else None)

        for p in numeric:
            from_spin = make_param_spin(p)
            to_spin = make_param_spin(p)
            if p.name == primary:
                from_spin.setValue(p.min if p.min is not None else 0.0)
                to_spin.setValue(p.max if p.max is not None else 1.0)
            else:
                from_spin.setValue(p.default)
                to_spin.setValue(p.default)
            for spin in (from_spin, to_spin):
                spin.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

            row = QWidget()
            row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            box = QHBoxLayout(row)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(6)
            # Fixed-width, centered arrow so it lines up across every row.
            arrow = QLabel("→")
            arrow.setFixedWidth(14)
            arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
            arrow.setStyleSheet("color: #9a9aa6;")
            box.addWidget(from_spin, 1)
            box.addWidget(arrow)
            box.addWidget(to_spin, 1)
            self._trans_form.addRow(p.label, row)
            self._trans_spins[p.name] = (from_spin, to_spin)

    # -- actions ----------------------------------------------------------- #

    def open_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open image", "", _SUPPORTED)
        if not path:
            return
        try:
            self._load_image(path)
        except Exception as exc:  # noqa: BLE001 — surface any decode error to the user
            QMessageBox.critical(self, "Could not open image", str(exc))

    def _load_image(self, path: str) -> None:
        """Load ``path`` as the working image and render the preview."""
        image = Image.open(path).convert("RGB")
        self._original = image
        self._source_path = Path(path)
        self._preview_src = downscale(image, _PREVIEW_MAX)
        self._save_btn.setEnabled(True)
        self._video_btn.setEnabled(True)
        self._status.setText(f"{Path(path).name} — {image.width}×{image.height}px")
        self._compare.set_before(pil_to_qpixmap(self._preview_src))
        self._render_preview()

    def _load_default_image(self) -> None:
        """On startup, auto-load the default image from the workspace if present."""
        if not _DEFAULT_IMAGE.is_file():
            return
        try:
            self._load_image(str(_DEFAULT_IMAGE))
        except Exception:  # noqa: BLE001 — a broken default shouldn't block startup
            pass

    def save_image(self) -> None:
        if self._original is None or self._effect is None:
            return
        suggested = "output.png"
        path, _ = QFileDialog.getSaveFileName(self, "Save image",
                                              suggested, _SUPPORTED)
        if not path:
            return
        try:
            # Render at full resolution for the export.
            result = self._effect.apply(self._original, **self._controls.values())
            result.save(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Could not save image", str(exc))
            return
        self._status.setText(f"Saved {Path(path).name}")

    # -- video export ------------------------------------------------------ #

    def export_video(self) -> None:
        if (self._original is None or self._effect is None
                or self._source_path is None or self._video_worker is not None):
            return

        transitions = {name: (frm.value(), to.value())
                       for name, (frm, to) in self._trans_spins.items()}
        if not transitions:
            QMessageBox.information(self, "Render video",
                                   "This effect has no numeric parameter to animate.")
            return

        default_out = str(self._source_path.with_suffix(".mp4"))
        path, _ = QFileDialog.getSaveFileName(self, "Render video", default_out,
                                              "Video (*.mp4)")
        if not path:
            return

        duration = self._dur_spin.value()
        fps = self._fps_spin.value()
        frames = self._frames_spin.value()
        max_size = None if self._native_check.isChecked() else self._size_spin.value()
        smooth = self._smooth_combo.currentData()

        self._video_progress = QProgressDialog("Rendering video…", "Cancel", 0, frames, self)
        self._video_progress.setWindowTitle("Render video")
        self._video_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._video_progress.setMinimumDuration(0)
        self._video_progress.setValue(0)

        worker = VideoWorker(self._original, self._effect, path, self._source_path,
                             transitions, self._controls.values(), duration, fps, frames,
                             max_size, smooth, self)
        worker.progress.connect(self._on_video_progress)
        worker.done.connect(self._on_video_done)
        worker.failed.connect(self._on_video_failed)
        self._video_progress.canceled.connect(worker.cancel)
        self._video_worker = worker

        self._video_btn.setEnabled(False)
        animated = ", ".join(n for n, (a, b) in transitions.items() if a != b)
        self._status.setText(f"Rendering video ({animated or 'no change'})…")
        worker.start()

    def _on_video_progress(self, done: int, total: int) -> None:
        if self._video_progress is not None:
            self._video_progress.setMaximum(total)
            self._video_progress.setValue(done)
            self._video_progress.setLabelText(f"Rendering frame {done} / {total}…")

    def _on_video_done(self, out: object) -> None:
        self._finish_video()
        if out is None:
            self._status.setText("Video cancelled.")
        else:
            self._status.setText(f"Saved {Path(out).name}")

    def _on_video_failed(self, message: str) -> None:
        self._finish_video()
        QMessageBox.critical(self, "Could not render video", message)
        self._status.setText("Video failed.")

    def _finish_video(self) -> None:
        if self._video_progress is not None:
            self._video_progress.reset()
            self._video_progress = None
        self._video_worker = None
        self._video_btn.setEnabled(self._original is not None)

    # -- preview pipeline -------------------------------------------------- #

    def _on_effect_changed(self, index: int) -> None:
        self._effect = self._effect_combo.itemData(index)
        if self._effect is not None:
            self._controls.set_effect(self._effect)
            self._rebuild_transitions(self._effect)
        self._render_preview()

    def _on_controls_changed(self, _values: dict) -> None:
        # Coalesce rapid slider updates into a single render.
        self._timer.start()

    def _render_preview(self) -> None:
        if self._preview_src is None or self._effect is None:
            return
        # Only one preview renders at a time; if settings change mid-render,
        # remember to render again (with the latest values) when it finishes.
        if self._preview_thread is not None:
            self._preview_dirty = True
            return
        self._preview_dirty = False
        worker = PreviewWorker(self._effect, self._preview_src,
                               self._controls.values(), self)
        worker.done.connect(self._on_preview_done)
        worker.failed.connect(self._on_preview_failed)
        worker.finished.connect(worker.deleteLater)
        self._preview_thread = worker
        worker.start()
        self._spinner_delay.start()

    def _on_preview_done(self, image: object) -> None:
        self._compare.set_after(pil_to_qpixmap(image))
        self._end_preview()

    def _on_preview_failed(self, message: str) -> None:
        self._status.setText(f"Preview failed: {message}")
        self._end_preview()

    def _end_preview(self) -> None:
        self._preview_thread = None
        self._spinner_delay.stop()
        self._spinner.stop()
        if self._preview_dirty:
            self._render_preview()

    def _show_spinner(self) -> None:
        if self._preview_thread is not None:   # still rendering
            self._center_spinner()
            self._spinner.start()

    def _center_spinner(self) -> None:
        area = self._compare.rect()
        size = self._spinner.size()
        self._spinner.move(area.center().x() - size.width() // 2,
                           area.center().y() - size.height() // 2)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt naming)
        if obj is self._compare and event.type() == QEvent.Type.Resize:
            self._center_spinner()
        return super().eventFilter(obj, event)
