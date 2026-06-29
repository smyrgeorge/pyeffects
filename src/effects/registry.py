"""
Registry of available effects.

Add new effects here and they automatically appear in the GUI's effect
selector. Each entry is a zero-argument factory so the UI can create fresh,
independent instances.
"""

from __future__ import annotations

from effects.base import Effect
from effects.glitch import GlitchEffect
from effects.glitch_height import GlitchHeightEffect
from effects.night import NightEffect
from effects.pixelate import PixelateEffect
from effects.popart import PopArtEffect

#: Ordered list of effect factories shown in the UI (alphabetical by name).
EFFECT_FACTORIES: list[type[Effect]] = [
    GlitchEffect,
    GlitchHeightEffect,
    NightEffect,
    PixelateEffect,
    PopArtEffect,
]


def available_effects() -> list[Effect]:
    """Instantiate and return one of every registered effect."""
    return [factory() for factory in EFFECT_FACTORIES]
