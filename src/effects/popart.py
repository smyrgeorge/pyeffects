#!/usr/bin/env python3
"""
Pop Art Effect

A Warhol-style silkscreen: the image is laid out as a 2x2 grid and each
quadrant is recoloured through a different two/three-tone gradient map, then
overlaid with a halftone dot screen and a subtle paper texture.

It can be used three ways:

  * Programmatically:      PopArtEffect().apply(image, strength=1.0, ...)
  * From the GUI:          python src/ui/app.py
  * From the command line:  python src/effects/popart.py workspace/photo.jpg
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any

import numpy as np
from PIL import Image, ImageFilter

from effects.base import Effect, Param, ParamKind
from utils.cli import run_cli

_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)

#: Four duotone gradient maps (shadow -> highlight), one per grid quadrant, in
#: reading order: top-left, top-right, bottom-left, bottom-right. Each is a list
#: of (position 0..1, RGB 0..255) stops. Sampled from a reference silkscreen.
_PALETTES = [
    [   # top-left: maroon shadows -> olive mids -> pale-green highlights
        (0.00, (122, 44, 42)),
        (0.20, (158, 88, 73)),
        (0.40, (168, 136, 104)),
        (0.60, (184, 185, 138)),
        (0.80, (197, 212, 162)),
        (1.00, (233, 250, 221)),
    ],
    [   # top-right: magenta shadows -> violet -> periwinkle highlights
        (0.00, (165, 55, 203)),
        (0.20, (181, 94, 225)),
        (0.40, (179, 142, 231)),
        (0.60, (184, 189, 238)),
        (0.80, (191, 217, 243)),
        (1.00, (236, 252, 252)),
    ],
    [   # bottom-left: blue shadows -> cyan -> pale-yellow highlights
        (0.00, (38, 96, 188)),
        (0.20, (67, 134, 204)),
        (0.40, (101, 166, 208)),
        (0.60, (165, 199, 183)),
        (0.80, (200, 221, 184)),
        (1.00, (247, 247, 194)),
    ],
    [   # bottom-right: dark-green shadows -> mauve -> pink highlights
        (0.00, (48, 80, 20)),
        (0.20, (87, 119, 51)),
        (0.40, (136, 145, 107)),
        (0.60, (186, 173, 168)),
        (0.80, (216, 190, 206)),
        (1.00, (252, 251, 253)),
    ],
]


class PopArtEffect(Effect):
    """A Warhol-style four-up silkscreen with a halftone dot screen."""

    id = "popart"
    name = "Pop Art"

    def params(self) -> list[Param]:
        return [
            Param(
                name="strength", label="Strength", kind=ParamKind.FLOAT,
                default=1.0, min=0.0, max=1.0, step=0.01,
                help="Blend between the original and the full pop-art result.",
            ),
            Param(
                name="contrast", label="Contrast", kind=ParamKind.FLOAT,
                default=0.5, min=0.0, max=1.0, step=0.01,
                help="Tonal punch before the colour mapping (higher = bolder).",
            ),
            Param(
                name="dots", label="Halftone", kind=ParamKind.FLOAT,
                default=0.5, min=0.0, max=1.0, step=0.01,
                help="Strength of the halftone dot screen.",
            ),
            Param(
                name="dot_size", label="Dot size", kind=ParamKind.FLOAT,
                default=0.010, min=0.004, max=0.04, step=0.001,
                help="Halftone cell size as a fraction of the quadrant width.",
            ),
            Param(
                name="texture", label="Paper texture", kind=ParamKind.FLOAT,
                default=0.3, min=0.0, max=1.0, step=0.01,
                help="Amount of canvas/paper grain over the print.",
            ),
        ]

    def apply(self, image: Image.Image, **values: Any) -> Image.Image:
        v = self.merge(values)
        strength = _clamp(float(v["strength"]))
        contrast = _clamp(float(v["contrast"]))
        dots = _clamp(float(v["dots"]))
        dot_size = max(0.004, float(v["dot_size"]))
        texture = _clamp(float(v["texture"]))

        rgb = image.convert("RGB")
        W, H = rgb.width, rgb.height
        tw, th = max(1, W // 2), max(1, H // 2)

        # One shared tonal base for every quadrant (the same photo, recoloured).
        small = rgb.resize((tw, th), Image.LANCZOS)
        arr = np.asarray(small).astype(np.float32) / 255.0
        lum = arr @ _LUMA

        # Spread the tones: normalise to the image's own range, then S-curve.
        lo, hi = np.percentile(lum, 2), np.percentile(lum, 98)
        tone = np.clip((lum - lo) / max(1e-6, hi - lo), 0.0, 1.0)
        tone = _contrast(tone, 0.25 + contrast * 0.9)

        cell = max(2, int(round(dot_size * tw)))
        screen = _halftone_mask(tone, cell) if dots > 0.0 else None

        canvas = Image.new("RGB", (tw * 2, th * 2))
        idx = np.clip(tone * 255.0, 0, 255).astype(np.int32)
        for i, palette in enumerate(_PALETTES):
            lut = _build_lut(palette)
            tile = lut[idx]                                   # (th, tw, 3), 0..1
            if screen is not None:
                tile = tile * (1.0 - dots * 0.55 * screen[..., None])
            if texture > 0.0:
                tile = _apply_texture(tile, texture, seed=i)
            tile_img = Image.fromarray((np.clip(tile, 0, 1) * 255).astype(np.uint8))
            canvas.paste(tile_img, ((i % 2) * tw, (i // 2) * th))

        out = canvas.resize((W, H), Image.LANCZOS)

        if strength < 1.0:
            out = Image.blend(rgb, out, strength)
        return out


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _build_lut(palette) -> np.ndarray:
    """Interpolate palette control points into a (256, 3) LUT in 0..1."""
    xs = np.array([p[0] for p in palette], dtype=np.float32)
    cols = np.array([p[1] for p in palette], dtype=np.float32) / 255.0
    grid = np.linspace(0.0, 1.0, 256, dtype=np.float32)
    lut = np.stack([np.interp(grid, xs, cols[:, c]) for c in range(3)], axis=1)
    return lut.astype(np.float32)


def _contrast(tone: np.ndarray, amount: float) -> np.ndarray:
    """A smooth S-curve around 0.5; `amount` ~0..1.15 controls the steepness."""
    return np.clip(0.5 + (np.tanh((tone - 0.5) * (1.0 + amount * 3.0))
                          / np.tanh(0.5 * (1.0 + amount * 3.0))) * 0.5, 0.0, 1.0)


def _halftone_mask(tone: np.ndarray, cell: int) -> np.ndarray:
    """
    A halftone dot screen in 0..1 (1 = full ink dot, 0 = paper). The dot in each
    cell grows with that cell's darkness, giving the classic print screen.
    """
    h, w = tone.shape
    # Per-cell ink: average tone over each cell, held constant across the cell.
    small = Image.fromarray((np.clip(tone, 0, 1) * 255).astype(np.uint8))
    cells = small.resize((max(1, w // cell), max(1, h // cell)), Image.BOX)
    ink = 1.0 - np.asarray(cells.resize((w, h), Image.NEAREST)).astype(np.float32) / 255.0

    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    # Distance from each pixel to its cell centre, normalised to the half-cell.
    dx = (xs % cell) - (cell - 1) / 2.0
    dy = (ys % cell) - (cell - 1) / 2.0
    dist = np.sqrt(dx * dx + dy * dy) / (cell / 2.0)
    # Dot radius grows toward the shadows but is capped below 1 so the dots
    # never fully merge — the screen stays visible even in the darkest fields.
    radius = 0.24 + 0.52 * np.sqrt(ink)
    return np.clip((radius - dist) / 0.30, 0.0, 1.0)


def _apply_texture(tile: np.ndarray, amount: float, seed: int) -> np.ndarray:
    """Multiply in a subtle canvas weave plus fine grain."""
    h, w = tile.shape[:2]
    rng = np.random.default_rng(1234 + seed)
    # Grain centred on 128 so a gaussian blur keeps it zero-mean.
    noise = (rng.normal(0.0, 1.0, size=(h, w)).astype(np.float32) * 40.0 + 128.0)
    blurred = Image.fromarray(np.clip(noise, 0, 255).astype(np.uint8)) \
        .filter(ImageFilter.GaussianBlur(0.6))
    grain = (np.asarray(blurred).astype(np.float32) - 128.0) / 128.0
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    weave = (np.sin(xs * 1.4) + np.sin(ys * 1.4)) * 0.5
    tex = 1.0 + amount * (0.10 * grain + 0.03 * weave)
    return tile * tex[..., None]


if __name__ == "__main__":
    try:
        sys.exit(run_cli(PopArtEffect()))
    except KeyboardInterrupt:
        print("\n\n⚠️  Cancelled by user (Ctrl+C)")
        sys.exit(130)
