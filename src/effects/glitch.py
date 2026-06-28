#!/usr/bin/env python3
"""
Glitch Effect

Applies a "glitch art" effect to an image. The effect combines four classic
techniques, each independently configurable:

  1. RGB channel shift (chromatic aberration) — the red and blue channels are
     offset horizontally from the green channel.
  2. Random horizontal slice displacement — random bands of the image are
     shifted left/right to mimic a corrupted scan.
  3. Color noise — random speckle added across the image.
  4. Scanlines — alternating darkened rows for an old-CRT look.

It can be used three ways:

  * Programmatically:      GlitchEffect().apply(image, rgb_shift=0.5, ...)
  * From the GUI:          python src/ui/app.py
  * From the command line:  python src/effects/glitch.py workspace/photo.jpg
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


class GlitchEffect(Effect):
    """A configurable glitch-art effect."""

    id = "glitch"
    name = "Glitch"

    def params(self) -> list[Param]:
        return [
            Param(
                name="rgb_shift", label="RGB shift", kind=ParamKind.FLOAT,
                default=0.5, min=0.0, max=1.0, step=0.01,
                help="Chromatic aberration: how far the red/blue channels slide.",
            ),
            Param(
                name="slices", label="Slices", kind=ParamKind.INT,
                default=20, min=0, max=80, step=1,
                help="Number of horizontal bands that get displaced.",
            ),
            Param(
                name="slice_shift", label="Slice shift", kind=ParamKind.FLOAT,
                default=0.10, min=0.0, max=1.0, step=0.01,
                help="Maximum horizontal displacement of each band (fraction of width).",
            ),
            Param(
                name="noise", label="Noise", kind=ParamKind.FLOAT,
                default=0.0, min=0.0, max=1.0, step=0.01,
                help="Amount of random color speckle added to the image.",
            ),
            Param(
                name="scanlines", label="Scanlines", kind=ParamKind.FLOAT,
                default=0.15, min=0.0, max=1.0, step=0.01,
                help="Strength of the darkened CRT-style scanlines.",
            ),
            Param(
                name="seed", label="Seed", kind=ParamKind.INT,
                default=42, min=0, max=9999, step=1,
                help="Random seed. The same seed always produces the same glitch.",
            ),
        ]

    def apply(self, image: Image.Image, **values: Any) -> Image.Image:
        v = self.merge(values)
        rng = np.random.default_rng(int(v["seed"]))

        arr = np.asarray(image.convert("RGB"))
        height, width = arr.shape[:2]

        # Scale fractional parameters into pixel/count units.
        max_channel_shift = int(width * 0.08 * float(v["rgb_shift"]))
        num_slices = int(v["slices"])
        max_slice_shift = int(width * float(v["slice_shift"]))

        out = _shift_channels(arr, max_channel_shift, rng)
        out = _slice_displace(out, num_slices, max_slice_shift, rng)
        out = _add_noise(out, float(v["noise"]), rng)
        out = _scanlines(out, float(v["scanlines"]))

        return Image.fromarray(out)


def _shift_channels(arr: np.ndarray, max_shift: int, rng: np.random.Generator) -> np.ndarray:
    """Apply chromatic aberration by horizontally rolling the R and B channels."""
    out = arr.copy()
    if max_shift <= 0:
        return out

    red_shift = int(rng.integers(-max_shift, max_shift + 1))
    blue_shift = int(rng.integers(-max_shift, max_shift + 1))

    out[:, :, 0] = np.roll(arr[:, :, 0], red_shift, axis=1)
    out[:, :, 2] = np.roll(arr[:, :, 2], blue_shift, axis=1)
    return out


def _slice_displace(arr: np.ndarray, num_slices: int, max_shift: int,
                    rng: np.random.Generator) -> np.ndarray:
    """Displace random horizontal bands of the image to the left or right."""
    out = arr.copy()
    height = arr.shape[0]
    if num_slices <= 0 or max_shift <= 0 or height < 2:
        return out

    for _ in range(num_slices):
        y0 = int(rng.integers(0, height - 1))
        band_height = int(rng.integers(1, max(2, height // 20)))
        y1 = min(y0 + band_height, height)
        shift = int(rng.integers(-max_shift, max_shift + 1))
        out[y0:y1] = np.roll(arr[y0:y1], shift, axis=1)
    return out


def _add_noise(arr: np.ndarray, amount: float, rng: np.random.Generator) -> np.ndarray:
    """Add random color speckle scaled by ``amount`` (0-1)."""
    if amount <= 0:
        return arr
    noise = rng.normal(0.0, 255.0 * amount * 0.5, size=arr.shape)
    out = arr.astype(np.float32) + noise
    return np.clip(out, 0, 255).astype(np.uint8)


def _scanlines(arr: np.ndarray, strength: float) -> np.ndarray:
    """Darken every other row to mimic CRT scanlines. ``strength`` is 0-1."""
    if strength <= 0:
        return arr
    out = arr.astype(np.float32)
    out[::2, :, :3] *= (1.0 - strength)
    return np.clip(out, 0, 255).astype(np.uint8)


if __name__ == "__main__":
    try:
        sys.exit(run_cli(GlitchEffect()))
    except KeyboardInterrupt:
        print("\n\n⚠️  Cancelled by user (Ctrl+C)")
        sys.exit(130)  # Standard exit code for SIGINT
