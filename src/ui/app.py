#!/usr/bin/env python3
"""
PyEffects — desktop application entry point.

A reusable window for previewing image effects: a before/after slider, controls
built dynamically from whichever effect is selected, and still/video export.
The window itself lives in :mod:`ui.main_window`; this module just launches it.

Run with:
    python src/ui/app.py
"""

# Allow running this file directly: put the "src" directory on the import path.
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication, QMessageBox

from effects.registry import available_effects
from ui.main_window import MainWindow

__all__ = ["MainWindow", "main"]


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("PyEffects")

    # Qt's event loop otherwise swallows SIGINT, so Ctrl+C in the terminal does
    # nothing. Restoring the default handler lets it terminate the app.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    effects = available_effects()
    if not effects:
        QMessageBox.critical(None, "PyEffects", "No effects are registered.")
        return 1

    window = MainWindow(effects)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
