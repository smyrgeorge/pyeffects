#!/usr/bin/env python3
"""
Pixelate Effect

Turns the image into a blocky mosaic — the classic "pixel art" / censored look.
The image is shrunk down so each output block becomes a single averaged pixel,
then scaled back up with nearest-neighbour sampling so the blocks stay hard.

Two optional extras give it a retro-console feel:

  * Color levels — quantise each channel to a handful of values (posterise) for
    banded, 8-bit-style colour.
  * Smooth blocks — scale back up with bilinear sampling for soft, rounded
    blocks instead of hard squares.

The block size is expressed as a fraction of the image's longest edge rather
than an absolute pixel count, so the result looks identical in the (downscaled)
preview and the full-resolution export.

It can be used three ways:

  * Programmatically:      PixelateEffect().apply(image, pixel_size=0.05, ...)
  * From the GUI:          python src/ui/app.py
  * From the command line:  python src/effects/pixelate.py workspace/photo.jpg
"""

# Allow running this file directly: put the "src" directory on the import path
# so absolute imports (effects.*, utils.*) resolve the same way they do in the GUI.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any

import numpy as np
from PIL import Image

from effects.base import Effect, Param, ParamKind
from utils.cli import run_cli


class PixelateEffect(Effect):
    """A configurable mosaic / pixel-art effect."""

    id = "pixelate"
    name = "Pixelate"

    def params(self) -> list[Param]:
        return [
            Param(
                name="pixel_size", label="Pixel size", kind=ParamKind.FLOAT,
                default=0.03, min=0.005, max=0.2, step=0.005,
                help="Block size as a fraction of the image's longest edge "
                     "(bigger = chunkier blocks).",
            ),
            Param(
                name="levels", label="Color levels", kind=ParamKind.INT,
                default=256, min=2, max=256, step=1,
                help="Quantise each color channel to this many levels "
                     "(256 = full color, lower = retro banding).",
            ),
            Param(
                name="smooth", label="Smooth blocks", kind=ParamKind.BOOL,
                default=False,
                help="Soften the blocks with bilinear upscaling instead of hard pixels.",
            ),
        ]

    def apply(self, image: Image.Image, **values: Any) -> Image.Image:
        v = self.merge(values)
        pixel_size = _clamp(float(v["pixel_size"]), 0.001, 1.0)
        levels = max(2, min(256, int(v["levels"])))
        smooth = bool(v["smooth"])

        rgb = image.convert("RGB")
        width, height = rgb.size

        # Derive the block size in pixels from the fractional size, relative to the
        # longest edge, so the look is identical at preview and export resolutions.
        block = max(1, round(pixel_size * max(width, height)))
        cols = max(1, round(width / block))
        rows = max(1, round(height / block))

        # Shrink (area-averaging each block to one pixel) then enlarge back. Nearest
        # keeps the blocks crisp; bilinear gives the optional soft-block variant.
        small = rgb.resize((cols, rows), Image.BOX)
        if levels < 256:
            small = _posterize(small, levels)
        resample = Image.BILINEAR if smooth else Image.NEAREST
        return small.resize((width, height), resample)


# --------------------------------------------------------------------------- #
# Image-processing helpers
# --------------------------------------------------------------------------- #

def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _posterize(image: Image.Image, levels: int) -> Image.Image:
    """Quantise each channel to ``levels`` evenly-spaced values (2-256)."""
    arr = np.asarray(image).astype(np.float32)
    step = 255.0 / (levels - 1)
    arr = np.round(arr / step) * step
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


if __name__ == "__main__":
    try:
        sys.exit(run_cli(PixelateEffect()))
    except KeyboardInterrupt:
        print("\n\n⚠️  Cancelled by user (Ctrl+C)")
        sys.exit(130)  # Standard exit code for SIGINT
