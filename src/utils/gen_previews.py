#!/usr/bin/env python3
"""
Generate the before/after preview images shown in the README (``docs/img``).

For every registered effect that does **not** already have a preview image, this
renders the app's real before/after compare panel — reusing
:class:`ui.compare.CompareView` headless, so the output is pixel-for-pixel the
same widget the GUI shows — over a sample photo, and writes a ``.png`` under
``docs/img``. Each file is kept under a size budget (1 MB by default) by scaling
down until it fits.

Effects that already have a preview are skipped, so it is safe to run after
adding a new effect: only the missing previews are built (it never touches the
``.gif``). Pass ``--force`` to rebuild anyway.

The preview filename is the kebab-cased effect *name*, matching the existing
files: "Glitch Height" -> ``glitch-height.png``.

Usage:
    python src/utils/gen_previews.py
    python src/utils/gen_previews.py --source path/to/photo.jpg --force
    python src/utils/gen_previews.py --effect night --force
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

# Render without a display. Set before any QApplication is created; harmless if
# one already exists (e.g. when imported from the running GUI).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from effects.base import Effect
from effects.registry import available_effects
from utils.term import Colors, Icons

#: Repo root (……/src/utils/gen_previews.py -> parents[2]).
_ROOT = Path(__file__).resolve().parents[2]
_DOCS_IMG = _ROOT / "docs" / "img"
_DEFAULT_SOURCE = _DOCS_IMG / "sample.jpg"

#: 1 MB, the upper bound for any generated preview.
_DEFAULT_MAX_BYTES = 1_000_000
#: Logical width of the compare panel; the output is this times ``scale`` wide.
_DEFAULT_WIDTH = 800
_DEFAULT_SCALE = 2


def preview_filename(effect: Effect) -> str:
    """``GlitchHeightEffect`` (name "Glitch Height") -> ``glitch-height.png``."""
    return effect.name.strip().lower().replace(" ", "-") + ".png"


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _qimage_to_pil(qimage) -> Image.Image:
    """Convert a QImage to a Pillow RGB image (via an in-memory PNG)."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    qimage.save(buffer, "PNG")
    buffer.close()
    return Image.open(io.BytesIO(bytes(data.data()))).convert("RGB")


def render_compare(before: Image.Image, after: Image.Image,
                   width: int = _DEFAULT_WIDTH, scale: int = _DEFAULT_SCALE) -> Image.Image:
    """
    Render the before/after compare panel (the same widget the GUI uses) to a
    Pillow image. ``width`` is the logical panel width; the result is
    ``width * scale`` px wide, with the divider centred.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    from ui.compare import CompareView
    from ui.qt_image import pil_to_qpixmap

    QApplication.instance() or QApplication(["gen_previews"])

    # Size the panel to the image's aspect so it fills edge-to-edge (no letterbox).
    logical_h = max(1, round(width * after.height / after.width))

    view = CompareView()
    view.set_before(pil_to_qpixmap(before))
    view.set_after(pil_to_qpixmap(after))
    view.resize(width, logical_h)

    image = QImage(width * scale, logical_h * scale, QImage.Format.Format_RGBA8888)
    image.setDevicePixelRatio(scale)
    image.fill(Qt.GlobalColor.transparent)
    view.render(image)
    return _qimage_to_pil(image)


def _png_bytes(img: Image.Image) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _save_under_budget(img: Image.Image, path: Path, max_bytes: int) -> tuple[tuple[int, int], int]:
    """Save ``img`` as PNG, scaling down until it fits ``max_bytes``."""
    data = _png_bytes(img)
    while len(data) > max_bytes and img.width > 480:
        new_w = int(img.width * 0.88)
        img = img.resize((new_w, round(new_w * img.height / img.width)), Image.LANCZOS)
        data = _png_bytes(img)
    path.write_bytes(data)
    return img.size, len(data)


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

def generate(source: Path, out_dir: Path, *, only: str | None = None, force: bool = False,
             max_bytes: int = _DEFAULT_MAX_BYTES, width: int = _DEFAULT_WIDTH,
             scale: int = _DEFAULT_SCALE) -> int:
    """Build every missing preview. Returns a process exit code."""
    if not source.exists():
        print(f"{Colors.RED}{Icons.ERROR} Sample image not found: {source}{Colors.RESET}")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    original = Image.open(source).convert("RGB")
    # Work at output resolution so the panel is crisp at 1:1.
    target_w = width * scale
    base = original.resize((target_w, round(target_w * original.height / original.width)),
                           Image.LANCZOS)

    effects = available_effects()
    if only is not None:
        effects = [e for e in effects if e.id == only or e.name.lower() == only.lower()]
        if not effects:
            print(f"{Colors.RED}{Icons.ERROR} No effect matching '{only}'{Colors.RESET}")
            return 1

    created = skipped = 0
    for effect in effects:
        dest = out_dir / preview_filename(effect)
        try:
            rel = dest.relative_to(_ROOT)
        except ValueError:
            rel = dest
        if dest.exists() and not force:
            print(f"{Colors.GRAY}{Icons.SKIP} {effect.name}: {rel} already exists, skipping{Colors.RESET}")
            skipped += 1
            continue

        print(f"{Colors.CYAN}{Icons.SPARKLES} {effect.name}: rendering {rel} …{Colors.RESET}")
        after = effect.apply(base)
        panel = render_compare(base, after, width=width, scale=scale)
        (w, h), nbytes = _save_under_budget(panel, dest, max_bytes)
        print(f"{Colors.GREEN}{Icons.SUCCESS} {effect.name}: {w}x{h}, {nbytes / 1024:.0f} KB{Colors.RESET}")
        created += 1

    print(f"\n{Icons.CHART} Done: {created} created, {skipped} skipped.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gen_previews.py",
        description="Generate missing docs/img before/after preview images.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-s", "--source", type=Path, default=_DEFAULT_SOURCE,
                        help="Sample photo to run the effects on")
    parser.add_argument("-o", "--out-dir", type=Path, default=_DOCS_IMG,
                        help="Directory to write the preview PNGs into")
    parser.add_argument("-e", "--effect", default=None,
                        help="Only (re)build this effect (id or name)")
    parser.add_argument("-f", "--force", action="store_true",
                        help="Rebuild previews that already exist")
    parser.add_argument("--max-bytes", type=int, default=_DEFAULT_MAX_BYTES,
                        help="Maximum size per preview in bytes")
    parser.add_argument("--width", type=int, default=_DEFAULT_WIDTH,
                        help="Logical compare-panel width (output is width*scale px)")
    parser.add_argument("--scale", type=int, default=_DEFAULT_SCALE,
                        help="Pixel scale factor for a crisp, retina-style render")
    args = parser.parse_args(argv)

    return generate(args.source, args.out_dir, only=args.effect, force=args.force,
                    max_bytes=args.max_bytes, width=args.width, scale=args.scale)


if __name__ == "__main__":
    raise SystemExit(main())
