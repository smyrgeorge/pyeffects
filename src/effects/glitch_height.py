#!/usr/bin/env python3
"""
Glitch Height Effect

A radial "height"/3D extrusion effect. The image bursts outward from a focal
point into pointed, feathery spikes that grow longer toward the edges — as if
every region were extruded into 3D needles pointing away from the centre.

The pipeline is:

  1. Radial extrusion — brightness is read as height. The image is composited
     with itself at progressively larger zoom factors about the focal point, but
     a sample is only allowed to extrude out to a given zoom step if it is
     "tall" enough: its contrast (distance from mid-grey) must reach that step.
     So high-contrast features (bright food, dark rim) shoot outward into long
     pointed spikes, while flat mid-tones (a smooth spoon) barely move. Because a
     pixel at radius ``r`` moves outward by ``r*(f-1)`` for a zoom ``f``, the
     spikes also lengthen with distance from the centre.
  2. Disc mask — the result is framed to the circle that fits the image, so the
     corners fall away to black (the signature round "Height" framing).
  3. Scanlines — optional darkened rows for a CRT-style finish.

Usage:
    python src/effects/glitch_height.py workspace/photo.jpg
    python src/effects/glitch_height.py workspace/photo.jpg --strength 0.6 --center-y 0.55
"""

# Allow running this file directly: put the "src" directory on the import path.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any

import numpy as np
from PIL import Image, ImageFilter

from effects.base import Effect, Param, ParamKind
from utils.cli import run_cli

_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)

#: Longest edge (px) the extrusion runs at; larger inputs are scaled down and
#: the result scaled back up. The effect is soft, so this is visually lossless.
_WORK_MAX = 2000

#: Gate exponent (>1). Lower gate thresholds at far steps let features extrude
#: further, lengthening the spikes for a stronger, more dramatic burst.
_GATE_POW = 1.5

#: Deterministic, spatially-smooth wobble added to the gate threshold. Breaks
#: the regular ring/grid pattern of the hard threshold into organic variation in
#: spike length. Smooth (not per-pixel) noise keeps it furry without speckle.
_GATE_JITTER = 0.10

#: Gaussian blur (px, at working resolution) applied to the extrusion to soften
#: the stepped edges into smooth spikes.
_SMOOTH_BLUR = 0.8


class GlitchHeightEffect(Effect):
    """A radial extrusion ("height") effect bursting from a focal point."""

    id = "height"
    name = "Glitch Height"

    def params(self) -> list[Param]:
        return [
            Param(
                name="strength", label="Strength", kind=ParamKind.FLOAT,
                default=0.25, min=0.0, max=1.0, step=0.01,
                help="Length of the radial extrusion spikes.",
            ),
            Param(
                name="center_x", label="Center X", kind=ParamKind.FLOAT,
                default=0.5, min=0.0, max=1.0, step=0.01,
                help="Horizontal position of the focal point (0 = left, 1 = right).",
            ),
            Param(
                name="center_y", label="Center Y", kind=ParamKind.FLOAT,
                default=0.5, min=0.0, max=1.0, step=0.01,
                help="Vertical position of the focal point (0 = top, 1 = bottom).",
            ),
            Param(
                name="detail", label="Detail", kind=ParamKind.INT,
                default=100, min=10, max=200, step=1,
                help="Number of extrusion steps. Higher is smoother but slower.",
            ),
            Param(
                name="circle", label="Circular frame", kind=ParamKind.BOOL,
                default=False,
                help="Mask the result to a circle, fading the corners to black.",
            ),
            Param(
                name="scanlines", label="Scanlines", kind=ParamKind.FLOAT,
                default=0.0, min=0.0, max=1.0, step=0.01,
                help="Strength of the darkened CRT-style scanlines.",
            ),
        ]

    def apply(self, image: Image.Image, **values: Any) -> Image.Image:
        v = self.merge(values)
        strength = _clamp(float(v["strength"]))
        steps = max(1, int(v["detail"]))
        scanlines = _clamp(float(v["scanlines"]))

        rgb = image.convert("RGB")
        width, height = rgb.size

        # The extrusion is low-frequency and its proportions scale with the image,
        # so run it on a bounded copy and resize the result back. This keeps the
        # (many) zoom-composites fast on big photos, avoids radial banding from
        # large per-step displacements, and makes the preview match the export.
        scale = min(1.0, _WORK_MAX / max(width, height))
        work = rgb if scale >= 1.0 else rgb.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))), Image.BILINEAR)
        ww, wh = work.size
        cx = float(v["center_x"]) * ww
        cy = float(v["center_y"]) * wh

        # 1) Radial extrusion spikes. A wide zoom range plus the lenient gate
        # gives strong, long pointed needles bursting from the focal point.
        max_zoom = 1.0 + 2.8 * strength
        out = _extrude(work, cx, cy, max_zoom, steps)

        # 2) Circular frame — fade the corners to black.
        if bool(v["circle"]):
            radius = min(cx, cy, ww - cx, wh - cy)
            out = _disc_mask(out, cx, cy, radius, feather=max(2.0, radius * 0.06))

        # Back to the original resolution.
        if scale < 1.0:
            out = np.asarray(Image.fromarray(_to_uint8(out)).resize(
                (width, height), Image.BILINEAR)).astype(np.float32)

        # 3) Scanlines (applied full-resolution so the lines stay crisp).
        if scanlines > 0.0:
            out[::2, :, :] *= (1.0 - scanlines)

        return Image.fromarray(_to_uint8(out))


# --------------------------------------------------------------------------- #
# Image-processing helpers
# --------------------------------------------------------------------------- #

def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _to_uint8(arr: np.ndarray) -> np.ndarray:
    return np.clip(arr, 0, 255).astype(np.uint8)


def _extrude(img: Image.Image, cx: float, cy: float,
             max_zoom: float, steps: int) -> np.ndarray:
    """
    Composite progressively zoomed copies of ``img`` about the focal point,
    gating each step by contrast so that brightness acts as height.

    At step ``k`` the image is zoomed so its content sits ``k/steps`` of the way
    to ``max_zoom``. A sample is only painted at that step if its contrast
    (distance from mid-grey, 0..1) clears a threshold that rises with ``k`` —
    i.e. it is "tall" enough to have extruded this far. High-contrast features
    therefore project outward into long pointed spikes while flat mid-tones stay
    put. A smooth jitter on the threshold gives the spikes a furry edge, and a
    final blur softens the stepped edges into smooth spikes.
    """
    width, height = img.size
    acc = np.asarray(img).astype(np.float32)
    jitter = _smooth_noise(height, width, _GATE_JITTER)

    for k in range(1, steps + 1):
        frac = k / steps
        factor = 1.0 + (max_zoom - 1.0) * frac
        if factor <= 1.0:
            continue
        big = img.resize((max(1, round(width * factor)), max(1, round(height * factor))),
                         Image.BILINEAR)
        left = round(cx * (factor - 1.0))
        top = round(cy * (factor - 1.0))
        scaled = np.asarray(big.crop((left, top, left + width, top + height))).astype(np.float32)

        contrast = np.abs((scaled @ _LUMA) / 255.0 - 0.5) * 2.0
        extruded = contrast >= frac ** _GATE_POW + jitter
        acc[extruded] = scaled[extruded]

    if _SMOOTH_BLUR > 0.0:
        acc = np.asarray(Image.fromarray(_to_uint8(acc)).filter(
            ImageFilter.GaussianBlur(_SMOOTH_BLUR))).astype(np.float32)
    return acc


def _smooth_noise(height: int, width: int, amplitude: float) -> np.ndarray:
    """
    A deterministic, spatially-smooth noise field in ``[-amplitude, amplitude]``.

    Low-resolution white noise is upsampled with bilinear interpolation, so
    neighbouring pixels vary together — soft clumps rather than per-pixel
    speckle. The clump count is fixed relative to the image, so the texture
    scale stays consistent across resolutions.
    """
    cells = 48
    lh = max(2, height // (max(height, width) // cells or 1))
    lw = max(2, width // (max(height, width) // cells or 1))
    small = np.random.default_rng(0).random((lh, lw), dtype=np.float32)
    up = np.asarray(Image.fromarray((small * 255).astype(np.uint8)).resize(
        (width, height), Image.BILINEAR)).astype(np.float32) / 255.0
    return (up - 0.5) * (2.0 * amplitude)


def _disc_mask(arr: np.ndarray, cx: float, cy: float,
               radius: float, feather: float = 2.0) -> np.ndarray:
    """Fade everything outside the circle of ``radius`` about the centre to black."""
    height, width = arr.shape[:2]
    ys, xs = np.mgrid[0:height, 0:width]
    r = np.hypot(xs - cx, ys - cy)
    mask = np.clip((radius - r) / max(1.0, feather), 0.0, 1.0)
    return arr * mask[..., None]


if __name__ == "__main__":
    try:
        sys.exit(run_cli(GlitchHeightEffect()))
    except KeyboardInterrupt:
        print("\n\n⚠️  Cancelled by user (Ctrl+C)")
        sys.exit(130)  # Standard exit code for SIGINT
