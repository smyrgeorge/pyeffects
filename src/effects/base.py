"""
Effect abstraction.

Every image effect implements the :class:`Effect` interface and declares the
settings it supports as a list of :class:`Param` descriptors. Both the GUI and
the command-line interface are built dynamically from that declaration, so
adding a new effect never requires touching the UI or the argument parser.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from PIL import Image


class ParamKind(str, Enum):
    """The kind of a configurable parameter, used to pick a UI control."""

    FLOAT = "float"   # rendered as a slider with a decimal value
    INT = "int"       # rendered as a slider with an integer value
    BOOL = "bool"     # rendered as a checkbox
    CHOICE = "choice"  # rendered as a dropdown


@dataclass(frozen=True)
class Param:
    """
    Describes a single configurable setting of an effect.

    Attributes:
        name: Programmatic key passed to :meth:`Effect.apply`.
        label: Human-friendly label shown in the UI.
        kind: One of :class:`ParamKind`.
        default: Default value.
        min: Minimum value (numeric kinds only).
        max: Maximum value (numeric kinds only).
        step: Increment between values (numeric kinds only).
        choices: Allowed values (CHOICE kind only).
        help: Short description shown as a tooltip / CLI help string.
    """

    name: str
    label: str
    kind: ParamKind
    default: Any
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: tuple[Any, ...] = field(default_factory=tuple)
    help: str = ""


class Effect(ABC):
    """Base class for all image effects."""

    #: Short, stable identifier (used in filenames and the CLI).
    id: str = ""
    #: Human-friendly name shown in the UI.
    name: str = ""

    @abstractmethod
    def params(self) -> list[Param]:
        """Return the list of configurable parameters this effect supports."""

    @abstractmethod
    def apply(self, image: Image.Image, **values: Any) -> Image.Image:
        """
        Apply the effect to ``image`` and return a new image.

        Missing values fall back to each parameter's default. Implementations
        should not mutate the input image.
        """

    def defaults(self) -> dict[str, Any]:
        """Return a mapping of every parameter name to its default value."""
        return {p.name: p.default for p in self.params()}

    def merge(self, values: dict[str, Any]) -> dict[str, Any]:
        """
        Merge ``values`` over the defaults, keeping only known parameter names.

        This lets callers pass a partial (or noisy) settings dict and still get
        a complete, valid set of values back.
        """
        merged = self.defaults()
        for key, value in values.items():
            if key in merged:
                merged[key] = value
        return merged
