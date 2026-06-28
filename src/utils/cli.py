"""
Shared command-line runner for effects.

Any effect can be exposed as a standalone CLI with a single call to
:func:`run_cli`. The options are generated from the effect's own ``params()``,
so the parser, the GUI controls, and the effect logic all stay in sync from one
declaration.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PIL import Image
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from effects.base import Effect, ParamKind
from utils.file import output_path
from utils.term import Colors, Icons


def build_parser(effect: Effect) -> argparse.ArgumentParser:
    """Create an argparse parser whose options mirror the effect's params."""
    parser = argparse.ArgumentParser(
        prog=f"{effect.id}.py",
        description=f"Apply the '{effect.name}' effect to an image file",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input_file", help="Path to the input image file")
    parser.add_argument(
        "-o", "--output-file", default=None,
        help=f"Path for the output image (default: '<name>_{effect.id}.<ext>' next to the input)",
    )

    for p in effect.params():
        flag = f"--{p.name.replace('_', '-')}"
        if p.kind is ParamKind.BOOL:
            parser.add_argument(flag, dest=p.name, action="store_true",
                                default=p.default, help=p.help)
        elif p.kind is ParamKind.CHOICE:
            parser.add_argument(flag, dest=p.name, choices=list(p.choices),
                                default=p.default, help=p.help)
        else:
            ptype = int if p.kind is ParamKind.INT else float
            parser.add_argument(flag, dest=p.name, type=ptype,
                                default=p.default, help=p.help)
    return parser


def run_cli(effect: Effect, argv: list[str] | None = None) -> int:
    """
    Parse arguments, apply ``effect`` to the input image and save the result.

    Returns a process exit code (0 on success, 1 on error).
    """
    parser = build_parser(effect)
    args = vars(parser.parse_args(argv))

    input_file = args.pop("input_file")
    out_file = args.pop("output_file") or str(output_path(input_file, suffix=effect.id))

    console = Console()
    try:
        if not os.path.exists(input_file):
            raise FileNotFoundError(f"Input file not found: {input_file}")

        image = Image.open(input_file).convert("RGB")

        header = Text()
        header.append("File: ", style="dim")
        header.append(f"{input_file}\n", style="cyan")
        header.append("Dimensions: ", style="dim")
        header.append(f"{image.width} x {image.height}\n", style="bold")
        for key, value in args.items():
            header.append(f"{key}: ", style="dim")
            header.append(f"{value}\n", style="bold")
        console.print("\n")
        console.print(Panel(header, title=f"[bold]{Icons.SPARKLES} {effect.name} Effect[/bold]",
                            border_style="cyan", expand=True, padding=(1, 2)))
        print()

        result = effect.apply(image, **args)

        out_path = Path(out_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        result.save(out_path)

        summary = Text()
        summary.append(f"{Icons.SUCCESS} Image saved\n", style="green bold")
        summary.append("Output: ", style="cyan")
        summary.append(f"{out_path}", style="cyan bold")
        console.print(Panel(summary, title=f"[bold]{Icons.CHART} Summary[/bold]",
                            border_style="magenta", expand=True, padding=(1, 2)))
        return 0
    except Exception as e:  # noqa: BLE001 — surface any failure to the user
        print(f"{Colors.RED}{Icons.ERROR} Error:{Colors.RESET} {e}")
        return 1
