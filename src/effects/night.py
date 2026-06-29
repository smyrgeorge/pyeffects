#!/usr/bin/env python3
"""
Night Effect

A neon "night" stylisation: the background is crushed to black while the
detailed, in-focus subject glows in a warm/cool palette — deep teals and blues
in the shadows, fiery oranges and ambers in the highlights — finished with
posterised banding, a vignette and a fine grain.

It can be used three ways:

  * Programmatically:      NightEffect().apply(image, strength=1.0, ...)
  * From the GUI:          python src/ui/app.py
  * From the command line:  python src/effects/night.py workspace/photo.jpg
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

#: Two gradient-map palettes keyed on luminance (0..1) -> RGB (0..255). The
#: final colour is a per-pixel blend of the two, chosen by how *warm-hued* the
#: source pixel is: neutral fur takes the COOL ramp (teal shadows -> cyan/white
#: highlights) while genuinely warm features — amber eyes, the nose, warm ear
#: skin — take the WARM ramp (teal shadows -> orange/amber). This reproduces the
#: reference's cool body with warm accents, instead of turning every bright
#: region (e.g. the muzzle) orange the way a single luminance ramp would.
_PALETTE_COOL = [
    (0.00, (0, 0, 0)),
    (0.12, (8, 14, 22)),       # near-black navy
    (0.26, (18, 44, 62)),      # teal/blue shadow
    (0.42, (30, 72, 90)),      # teal mid
    (0.58, (48, 100, 112)),    # teal-cyan
    (0.74, (84, 142, 150)),    # cyan
    (0.88, (164, 202, 208)),   # pale cyan
    (1.00, (234, 240, 242)),   # cool-white highlight (whiskers, chin)
]
_PALETTE_WARM = [
    (0.00, (0, 0, 0)),
    (0.12, (14, 12, 18)),      # near-black (shares the cool shadow)
    (0.26, (40, 36, 44)),      # neutral-dark
    (0.42, (118, 60, 40)),     # warm transition
    (0.58, (196, 92, 40)),     # orange
    (0.74, (232, 128, 48)),    # bright orange
    (0.88, (246, 176, 86)),    # amber (eyes)
    (1.00, (252, 224, 176)),   # warm pale highlight
]


class NightEffect(Effect):
    """A neon night-time stylisation."""

    id = "night"
    name = "Night"

    def params(self) -> list[Param]:
        return [
            Param(
                name="strength", label="Strength", kind=ParamKind.FLOAT,
                default=1.0, min=0.0, max=1.0, step=0.01,
                help="Blend between the original and the full night stylisation.",
            ),
            Param(
                name="detail", label="Detail glow", kind=ParamKind.FLOAT,
                default=0.5, min=0.0, max=1.0, step=0.01,
                help="How strongly textured/in-focus areas light up vs. fade to black.",
            ),
            Param(
                name="streaks", label="Streaks", kind=ParamKind.FLOAT,
                default=0.0, min=0.0, max=1.0, step=0.01,
                help="Boldness of the hard-edged streak/block shredding of the fur "
                     "(0 = smooth, 1 = chunky shards).",
            ),
            Param(
                name="levels", label="Color levels", kind=ParamKind.INT,
                default=8, min=2, max=32, step=1,
                help="Posterise each channel to this many levels (banding).",
            ),
            Param(
                name="grain", label="Grain", kind=ParamKind.FLOAT,
                default=0.035, min=0.0, max=0.3, step=0.01,
                help="Amount of fine film grain.",
            ),
            Param(
                name="vignette", label="Vignette", kind=ParamKind.FLOAT,
                default=0.7, min=0.0, max=1.0, step=0.01,
                help="Darkening of the corners toward black.",
            ),
        ]

    def apply(self, image: Image.Image, **values: Any) -> Image.Image:
        v = self.merge(values)
        strength = _clamp(float(v["strength"]))
        detail_amt = _clamp(float(v["detail"]))
        streaks = _clamp(float(v["streaks"]))
        levels = max(2, int(v["levels"]))
        grain = max(0.0, float(v["grain"]))
        vig = _clamp(float(v["vignette"]))

        rgb = image.convert("RGB")
        arr = np.asarray(rgb).astype(np.float32) / 255.0
        h, w = arr.shape[:2]

        lum = arr @ _LUMA

        # Presence mask = where the image stays lit (everything else -> black).
        # Two cues separate the in-focus subject from the smooth, blurred backdrop:
        #   * local contrast (fur, edges), and
        #   * sheer brightness (eyes, chest) so smooth-but-bright features survive.
        radius = max(1.0, min(h, w) * 0.012)
        blur = _gaussian(lum, radius)
        detail = np.abs(lum - blur)
        detail = detail / (np.percentile(detail, 99) + 1e-6)
        gain = 2.0 + detail_amt * 3.0
        presence_detail = np.clip(detail * gain, 0.0, 1.0)
        presence_bright = np.clip((lum - 0.62) / 0.25, 0.0, 1.0)
        presence = np.maximum(presence_detail, presence_bright)
        # Confine everything to the textured subject region: a wide blur of the
        # detail mask is high across the (busy) cat and ~0 over the smooth,
        # out-of-focus background, so stray bright background patches die to black.
        subject = _gaussian(presence_detail, radius * 4.0)
        subject = np.clip(subject * 3.0, 0.0, 1.0)
        # Suppress small, isolated bright specks (e.g. out-of-focus background
        # bokeh): only a large, connected textured area — the actual subject —
        # produces a strong response when blurred this wide, so lone highlights
        # die to black instead of becoming ringed blobs.
        region = _gaussian(presence_detail, radius * 14.0)
        region = np.clip(region * 5.0, 0.0, 1.0)
        subject = subject * region
        presence = presence * subject
        presence = _gaussian(presence, radius * 0.6)
        # Crush faint halos to pure black so the background reads dead-black
        # (the reference is ~two-thirds near-black).
        presence = np.clip((presence - 0.16) / 0.84, 0.0, 1.0)

        # Tone curve: push the blacks down, lift the mids hard so the lit subject
        # is vivid and bright (the background is already gated to black by
        # `presence`, so this only brightens the cat).
        tone = np.clip((lum - 0.12) / 0.74, 0.0, 1.0) ** 0.68

        # Per-pixel warmth of the *source* hue (R - B), smoothed so it follows
        # features (warm eyes/nose/ears) rather than single noisy pixels.
        warm_src = np.clip((arr[..., 0] - arr[..., 2]) / 0.15, 0.0, 1.0)
        warm_src = _gaussian(warm_src, radius * 1.5)
        warm_src = np.clip(warm_src * 1.6, 0.0, 1.0)

        # Gradient map by tone, blending the cool and warm ramps by source hue.
        lut_cool = _build_lut(_PALETTE_COOL)
        lut_warm = _build_lut(_PALETTE_WARM)
        idx = np.clip(tone * 255.0, 0, 255).astype(np.int32)
        ws = warm_src[..., None]
        mapped = lut_cool[idx] * (1.0 - ws) + lut_warm[idx] * ws   # (h, w, 3), 0..1

        out = mapped * presence[..., None]

        # Streaks: hard-edged streak/block shredding (configurable via `streaks`).
        # BOX down-sampling averages each block to a flat colour; NEAREST up-sampling
        # keeps the edges crisp -> the combed, shard-like brush strokes of the
        # reference. Gated by the detail mask so textured fur shreds boldly while
        # smooth bright features (eyes, nose) stay crisp.
        if streaks > 0.0:
            fx = _lerp(0.45, 0.06, streaks)    # horizontal compression (bolder = chunkier)
            fy = _lerp(0.6, 0.12, streaks)     # vertical blockiness -> squarer shards
            shard = _shred(out, fx, fy)
            s = _lerp(0.4, 1.0, streaks) * presence_detail[..., None]
            out = out * (1.0 - s) + shard * s

        # Bloom: a soft glow around the bright, lit areas (neon feel). Kept
        # modest so it doesn't lift the black background into a grey haze.
        glow = _gaussian_rgb(out, radius * 3.0)
        out = 1.0 - (1.0 - out) * (1.0 - glow * 0.4)

        # Vignette toward black.
        out *= _vignette_mask(h, w, vig)[..., None]

        # Saturation boost.
        out = _saturate(out, 1.75)

        # Posterise.
        out = np.round(out * (levels - 1)) / (levels - 1)

        # Grain — only over the lit subject, so the black background stays clean.
        if grain > 0.0:
            noise = np.random.default_rng(7).normal(0.0, grain, size=(h, w, 1)).astype(np.float32)
            out = out + noise * presence[..., None]

        out = np.clip(out, 0.0, 1.0)

        if strength < 1.0:
            out = arr * (1.0 - strength) + out * strength

        return Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8))


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _build_lut(palette) -> np.ndarray:
    """Interpolate the palette control points into a (256, 3) LUT in 0..1."""
    xs = np.array([p[0] for p in palette], dtype=np.float32)
    cols = np.array([p[1] for p in palette], dtype=np.float32) / 255.0
    grid = np.linspace(0.0, 1.0, 256, dtype=np.float32)
    lut = np.stack([np.interp(grid, xs, cols[:, c]) for c in range(3)], axis=1)
    return lut.astype(np.float32)


def _gaussian(channel: np.ndarray, radius: float) -> np.ndarray:
    img = Image.fromarray(np.clip(channel * 255.0, 0, 255).astype(np.uint8))
    out = img.filter(ImageFilter.GaussianBlur(radius))
    return np.asarray(out).astype(np.float32) / 255.0


def _gaussian_rgb(rgb: np.ndarray, radius: float) -> np.ndarray:
    img = Image.fromarray(np.clip(rgb * 255.0, 0, 255).astype(np.uint8))
    out = img.filter(ImageFilter.GaussianBlur(radius))
    return np.asarray(out).astype(np.float32) / 255.0


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _shred(rgb: np.ndarray, fx: float, fy: float) -> np.ndarray:
    """Hard-edged rectangular shards: BOX-average down, NEAREST up (a mosaic)."""
    h, w = rgb.shape[:2]
    img = Image.fromarray(np.clip(rgb * 255.0, 0, 255).astype(np.uint8))
    small = img.resize((max(1, int(w * fx)), max(1, int(h * fy))), Image.BOX)
    return np.asarray(small.resize((w, h), Image.NEAREST)).astype(np.float32) / 255.0


def _vignette_mask(h: int, w: int, amount: float) -> np.ndarray:
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = w / 2.0, h / 2.0
    r = np.sqrt(((xs - cx) / (w / 2.0)) ** 2 + ((ys - cy) / (h / 2.0)) ** 2)
    mask = 1.0 - amount * np.clip((r - 0.5) / 0.8, 0.0, 1.0) ** 2
    return np.clip(mask, 0.0, 1.0)


def _saturate(rgb: np.ndarray, factor: float) -> np.ndarray:
    gray = (rgb @ _LUMA)[..., None]
    return np.clip(gray + (rgb - gray) * factor, 0.0, 1.0)


if __name__ == "__main__":
    try:
        sys.exit(run_cli(NightEffect()))
    except KeyboardInterrupt:
        print("\n\n⚠️  Cancelled by user (Ctrl+C)")
        sys.exit(130)
