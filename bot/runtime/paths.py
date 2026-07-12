"""Path helpers for local Python and PyInstaller execution."""

from pathlib import Path
import sys


def get_app_directory() -> Path:
    """Return the directory used for persistent runtime files.

    In a PyInstaller executable this is the folder containing the exe. During
    normal Python execution it is the project root.
    """

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent

    return Path(__file__).resolve().parents[2]


def get_resource_directory() -> Path:
    """Return the directory used for bundled read-only resources."""

    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).resolve()  # type: ignore[attr-defined]

    return Path(__file__).resolve().parents[2]


def ensure_runtime_directories(app_directory: Path) -> tuple[Path, Path]:
    """Create and return the data and log directories."""

    data_directory = app_directory / "data"
    logs_directory = app_directory / "logs"
    data_directory.mkdir(parents=True, exist_ok=True)
    logs_directory.mkdir(parents=True, exist_ok=True)
    return data_directory, logs_directory
