#!/usr/bin/env python3
"""
Animated-parameter video renderer.

Animates one or more of an effect's parameters across the frames of a video and
encodes the result into an MP4. Each animated parameter is given a ``from → to``
range; every other parameter is held constant. By default it animates the
``strength`` of the effect, ramped from 0 to 100%.

How it works:

  1. The source image is scaled down to a manageable video size.
  2. ``frames`` distinct frames are rendered, one per evenly-spaced parameter
     value, into a folder named ``_<image-stem>`` next to the image.
  3. ffmpeg encodes those frames into an MP4 that lasts ``duration`` seconds at
     ``fps``. When fewer frames were rendered than ``duration * fps``, ffmpeg
     duplicates them (filler frames) so the result is smooth and the right
     length — so ``frames`` trades render time against smoothness.

It can be used three ways:

  * Programmatically:      render_video("photo.jpg", GlitchHeightEffect())
  * From the GUI:          the "Video…" button
  * From the command line:  python src/render/video.py workspace/photo.jpg

By default the video is written next to the image with the same name and a
``.mp4`` extension (use ``-o`` to override).
"""

# Allow running this file directly: put the "src" directory on the import path.
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from effects.base import Effect, ParamKind
from effects.registry import available_effects

#: Defaults shared by the CLI and the GUI.
DEFAULT_PARAM = "strength"
DEFAULT_DURATION = 10.0   # seconds
DEFAULT_FPS = 30
DEFAULT_FRAMES = 100      # distinct renders (0..100%); ffmpeg fills to fps*duration
DEFAULT_MAX_SIZE = 1024   # longest edge of the video, in pixels
DEFAULT_SMOOTH = "blend"  # frame interpolation: "blend", "motion", or "none"

#: Hard ceiling on the encoded video's longest edge. H.264 much above 4K exceeds
#: most hardware decoders (incl. Apple VideoToolbox), which then stutters/skips on
#: playback even though the file is valid — so even "native" rendering is capped
#: to this. Matches the GUI's max-size limit.
MAX_VIDEO_SIZE = 3840

ProgressFn = Callable[[int, int], None]
CancelFn = Callable[[], bool]
NotifyFn = Callable[[], None]
LogFn = Callable[[str], None]


def render_video(
    image: Image.Image | str | Path,
    effect: Effect,
    output: str | Path | None = None,
    *,
    source_path: str | Path | None = None,
    param: str = DEFAULT_PARAM,
    start: float | None = None,
    end: float | None = None,
    transitions: dict[str, tuple[float, float]] | None = None,
    duration: float = DEFAULT_DURATION,
    fps: int = DEFAULT_FPS,
    frames: int = DEFAULT_FRAMES,
    max_size: int | None = DEFAULT_MAX_SIZE,
    smooth: str = DEFAULT_SMOOTH,
    workers: int | None = None,
    frames_dir: str | Path | None = None,
    values: dict[str, Any] | None = None,
    on_progress: ProgressFn | None = None,
    should_cancel: CancelFn | None = None,
    on_encode: NotifyFn | None = None,
    on_log: LogFn | None = None,
) -> Path | None:
    """
    Render an MP4 that animates one or more parameters of ``effect`` across the
    given image.

    Args:
        image: A PIL image or a path to one. If a path, it also sets the default
            output and frames-folder names.
        effect: The effect to apply.
        output: Output ``.mp4`` path. Defaults to the image path with a ``.mp4``
            suffix.
        source_path: Used for default naming when ``image`` is already a PIL
            image. Required (with ``image`` a PIL image) if ``output`` and
            ``frames_dir`` are both omitted.
        param: Name of the parameter to animate (single-parameter shortcut).
        start, end: Range for ``param``. Default to its min/max.
        transitions: Map of ``{parameter: (start, end)}`` to animate several
            parameters at once. Overrides ``param``/``start``/``end`` when given.
        duration: Video length in seconds.
        fps: Output frames per second.
        smooth: Frame interpolation used to fill up to ``fps``: ``"blend"``
            (cross-faded), ``"motion"`` (motion-compensated) or ``"none"``
            (duplicated frames).
        frames: Number of distinct frames to render across the animation. ffmpeg
            duplicates them to reach ``fps * duration`` frames, so higher is
            smoother but slower.
        max_size: Longest edge of the rendered video (``None`` keeps full size).
        workers: Number of frames to render in parallel (default: CPU count).
        frames_dir: Where to write the PNG frames. Defaults to ``_<stem>`` next
            to the image.
        values: Other parameter values to hold constant (the animated parameters
            are overridden per frame). Defaults to the effect's defaults.
        on_progress: Called as ``(done, total)`` after each frame renders.
        should_cancel: Polled before each frame; return ``True`` to abort (the
            function then returns ``None``).
        on_encode: Called once, with no arguments, right before ffmpeg starts
            encoding (i.e. after every frame has rendered). Lets a UI switch from
            a frame-progress bar to an indeterminate "encoding" spinner.
        on_log: Called with each line ffmpeg emits while encoding (its progress
            stats and diagnostics). Lets a UI show a live encode log.

    Returns:
        The output path, or ``None`` if cancelled.
    """
    if isinstance(image, (str, Path)):
        source_path = Path(image)
        img = Image.open(image).convert("RGB")
    else:
        img = image.convert("RGB")
        source_path = Path(source_path) if source_path else None

    if (output is None or frames_dir is None) and source_path is None:
        raise ValueError("Provide a source path (or both output and frames_dir).")

    out_path = Path(output) if output else source_path.with_suffix(".mp4")
    fdir = Path(frames_dir) if frames_dir else source_path.parent / f"_{source_path.stem}"

    if transitions:
        ranges = {name: (float(a), float(b)) for name, (a, b) in transitions.items()}
    else:
        lo, hi = _param_range(effect, param, start, end)
        ranges = {param: (lo, hi)}
    kinds = {p.name: p.kind for p in effect.params()}
    base = dict(values) if values else {}

    frames = max(1, int(frames))
    fps = max(1, int(fps))
    duration = max(1e-3, float(duration))

    work = _prepare(img, max_size)

    fdir.mkdir(parents=True, exist_ok=True)
    for stale in fdir.glob("frame_*.png"):
        stale.unlink()

    def frame_values(t: float) -> dict[str, Any]:
        """Interpolate every animated parameter to position ``t`` in ``[0, 1]``."""
        vals = dict(base)
        for name, (a, b) in ranges.items():
            value = a + t * (b - a)
            if kinds.get(name) is ParamKind.INT:
                value = int(round(value))
            vals[name] = value
        return vals

    jobs = []
    for i in range(frames):
        t = i / (frames - 1) if frames > 1 else 0.0
        jobs.append((frame_values(t), fdir / f"frame_{i:05d}.png"))

    def render_one(job: tuple[dict[str, Any], Path]) -> int:
        # Each frame is independent; the effect is stateless and the source image
        # is only read, so frames render safely in parallel. The heavy work (PIL
        # resize, numpy) releases the GIL, so threads give a real speedup.
        frame_vals, path = job
        effect.apply(work, **frame_vals).save(path)
        return 0

    n_workers = max(1, int(workers) if workers else (os.cpu_count() or 1))
    done = 0
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futures = [pool.submit(render_one, job) for job in jobs]
        for future in as_completed(futures):
            if should_cancel is not None and should_cancel():
                pool.shutdown(wait=False, cancel_futures=True)
                return None
            future.result()  # surface any rendering error
            done += 1
            if on_progress is not None:
                on_progress(done, frames)

    # ffmpeg stretches the rendered frames over ``duration`` and resamples to
    # ``fps``, duplicating frames as needed so the video is smooth and exact.
    # The input rate is passed as an exact rational (e.g. "101/10"); a rounded
    # decimal makes the image2 demuxer mistime the frames.
    input_fps = Fraction(frames) / Fraction(str(duration))
    if on_encode is not None:
        on_encode()
    _encode(fdir, out_path, f"{input_fps.numerator}/{input_fps.denominator}",
            fps, duration, smooth, on_log=on_log)
    return out_path


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _param_range(effect: Effect, param: str, start: float | None,
                 end: float | None) -> tuple[float, float]:
    descriptor = next((p for p in effect.params() if p.name == param), None)
    if descriptor is None:
        names = ", ".join(p.name for p in effect.params())
        raise ValueError(f"Effect '{effect.name}' has no parameter '{param}'. Available: {names}")
    lo = start if start is not None else (descriptor.min if descriptor.min is not None else 0.0)
    hi = end if end is not None else (descriptor.max if descriptor.max is not None else 1.0)
    return float(lo), float(hi)


def _prepare(image: Image.Image, max_size: int | None) -> Image.Image:
    """Scale to fit ``max_size`` (longest edge) and force even dimensions for H.264.

    Even when ``max_size`` is ``None`` ("native"), the result is capped to
    ``MAX_VIDEO_SIZE``: H.264 much larger than 4K exceeds hardware decoders and
    stutters on playback, so an uncapped native render is effectively unplayable.
    """
    cap = min(max_size, MAX_VIDEO_SIZE) if max_size else MAX_VIDEO_SIZE
    width, height = image.size
    if max(width, height) > cap:
        scale = cap / max(width, height)
        width, height = round(width * scale), round(height * scale)
    width = max(2, width - width % 2)
    height = max(2, height - height % 2)
    if (width, height) != image.size:
        return image.resize((width, height), Image.LANCZOS)
    return image


def _ffmpeg_exe() -> str:
    """Locate an ffmpeg binary: the bundled imageio-ffmpeg one, else the system one."""
    try:
        import imageio_ffmpeg  # optional, ships a static ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 — fall back to a system install
        exe = shutil.which("ffmpeg")
        if exe:
            return exe
    raise RuntimeError(
        "ffmpeg was not found. Install it (e.g. `brew install ffmpeg`) "
        "or run `pip install imageio-ffmpeg`.")


def _encode(frames_dir: Path, output: Path, input_fps: str, fps: int,
            duration: float, smooth: str = DEFAULT_SMOOTH,
            on_log: LogFn | None = None) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _ffmpeg_exe(), "-y",
        "-framerate", input_fps,
        "-start_number", "0",
        "-i", str(frames_dir / "frame_%05d.png"),
    ]
    # Reach ``fps`` either by interpolating smooth in-between frames (removing the
    # stepped look) or, for "none", by plain frame duplication. ``minterpolate``
    # cannot extend past the last frame, so clone it (tpad) and trim back to the
    # exact ``duration`` — keeping the video length exact.
    if smooth in ("blend", "motion"):
        mode = ("mi_mode=mci:me_mode=bidir:vsbmc=1" if smooth == "motion"
                else "mi_mode=blend")
        cmd += ["-vf", f"tpad=stop_mode=clone:stop_duration=1,minterpolate=fps={fps}:{mode}",
                "-t", f"{duration:.6f}"]
    else:  # "none"
        cmd += ["-r", str(fps)]
    cmd += [
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-crf", "18", "-preset", "medium",
        "-movflags", "+faststart",
        str(output),
    ]

    # Stream ffmpeg's output so callers can show live progress. ffmpeg rewrites its
    # stats line in place with carriage returns, so split on both CR and LF to
    # surface each update as its own line. The full output is retained so a failure
    # can report a useful tail.
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, bufsize=0)
    assert proc.stderr is not None
    lines: list[str] = []
    buffer = b""
    while True:
        chunk = proc.stderr.read(256)
        if not chunk:
            break
        buffer += chunk
        segments = re.split(rb"[\r\n]", buffer)
        buffer = segments.pop()   # keep the trailing, possibly-incomplete segment
        for segment in segments:
            text = segment.decode("utf-8", "replace").strip()
            if text:
                lines.append(text)
                if on_log is not None:
                    on_log(text)
    leftover = buffer.decode("utf-8", "replace").strip()
    if leftover:
        lines.append(leftover)
        if on_log is not None:
            on_log(leftover)

    if proc.wait() != 0:
        tail = "\n".join(lines[-8:])
        raise RuntimeError(f"ffmpeg failed (exit {proc.returncode}):\n{tail}")


# --------------------------------------------------------------------------- #
# Command-line interface
# --------------------------------------------------------------------------- #

def _resolve_effect(effect_id: str) -> Effect:
    for effect in available_effects():
        if effect.id == effect_id:
            return effect
    ids = ", ".join(f.id for f in available_effects())
    raise SystemExit(f"Unknown effect '{effect_id}'. Available: {ids}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="video.py",
        description="Render a video that animates one or more of an effect's parameters.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input_file", help="Path to the input image file")
    parser.add_argument("-e", "--effect", default="height", help="Effect id to animate")
    parser.add_argument("-o", "--output", default=None,
                        help="Output .mp4 path (default: '<name>.mp4' next to the input)")
    parser.add_argument("-p", "--param", default=DEFAULT_PARAM,
                        help="Parameter to animate (when --transition is not given)")
    parser.add_argument("--transition", action="append", metavar="NAME:FROM:TO", default=None,
                        help="Animate a parameter from FROM to TO; repeat to animate several")
    parser.add_argument("-d", "--duration", type=float, default=DEFAULT_DURATION,
                        help="Video length in seconds")
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS, help="Frames per second")
    parser.add_argument("--frames", type=int, default=DEFAULT_FRAMES,
                        help="Distinct frames to render (higher = smoother, slower)")
    parser.add_argument("--max-size", type=int, default=DEFAULT_MAX_SIZE,
                        help="Longest edge of the video in pixels")
    parser.add_argument("--smooth", choices=("blend", "motion", "none"), default=DEFAULT_SMOOTH,
                        help="Frame interpolation used to smooth the motion")
    parser.add_argument("--workers", type=int, default=None,
                        help="Frames to render in parallel (default: CPU count)")
    args = parser.parse_args(argv)

    transitions = None
    if args.transition:
        transitions = {}
        for spec in args.transition:
            try:
                name, lo, hi = spec.split(":")
                transitions[name] = (float(lo), float(hi))
            except ValueError:
                print(f"Invalid --transition '{spec}'. Use NAME:FROM:TO (e.g. strength:0:1).")
                return 1

    if not os.path.exists(args.input_file):
        print(f"Input file not found: {args.input_file}")
        return 1

    effect = _resolve_effect(args.effect)

    from rich.console import Console
    from rich.markup import escape
    from rich.progress import (BarColumn, Progress, SpinnerColumn,
                               TaskProgressColumn, TextColumn, TimeRemainingColumn)

    console = Console()
    animated = ", ".join(transitions) if transitions else args.param
    console.print(f"\n[bold]Rendering[/bold] {effect.name} '{animated}' → "
                 f"[cyan]{args.duration:g}s @ {args.fps}fps[/cyan]\n")

    with Progress(SpinnerColumn(finished_text="[green]✓[/green]"),
                  TextColumn("[progress.description]{task.description}"), BarColumn(),
                  TaskProgressColumn(), TimeRemainingColumn(), console=console) as progress:
        task = progress.add_task("Rendering frames", total=args.frames)
        encode_task = None

        def bump(done: int, total: int) -> None:
            progress.update(task, completed=done, total=total)

        def on_encode() -> None:
            # Frames are done; ffmpeg now encodes (and can be slow). Switch to an
            # indeterminate spinner so the user knows it is still running.
            nonlocal encode_task
            progress.update(task, completed=args.frames)
            encode_task = progress.add_task("Encoding video", total=None)

        def on_log(line: str) -> None:
            # Show ffmpeg's latest line beside the encode spinner. Escape it so
            # ffmpeg's own brackets aren't parsed as rich markup.
            if encode_task is not None:
                progress.update(encode_task, description=f"Encoding · {escape(line[:70])}")

        try:
            out = render_video(
                args.input_file, effect, args.output,
                param=args.param, transitions=transitions, duration=args.duration, fps=args.fps,
                frames=args.frames, max_size=args.max_size, smooth=args.smooth,
                workers=args.workers, on_progress=bump, on_encode=on_encode, on_log=on_log)
        except Exception as exc:  # noqa: BLE001 — surface any failure to the user
            console.print(f"[red]Error:[/red] {exc}")
            return 1

        if encode_task is not None:
            progress.update(encode_task, total=1, completed=1, description="Encoding video")

    console.print(f"\n[green]✓ Saved[/green] [cyan]{out}[/cyan]\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Cancelled by user (Ctrl+C)")
        sys.exit(130)
