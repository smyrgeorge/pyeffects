import re
from pathlib import Path


def natural_sort_key(path: Path) -> list:
    """
    Generate a sort key for natural sorting of file names.
    This ensures that file10.ext comes after file2.ext, not before.

    Args:
        path: Path object to generate a sort key for.

    Returns:
        List of strings and integers for natural sorting.
    """
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", str(path.name))]


def output_path(input_file: str, suffix: str = "glitch") -> Path:
    """
    Build a sibling output path for a processed image.

    Example: photo.jpg -> photo_glitch.jpg

    Args:
        input_file: Path to the original image file.
        suffix: Suffix to append to the file stem.

    Returns:
        Path object pointing next to the input file.
    """
    path = Path(input_file)
    return path.parent / f"{path.stem}_{suffix}{path.suffix}"
