"""Product identity shared by runtime modules."""

import pathlib
import sys

NAME = "Reisülküttab"
SLUG = "reisulkuttab"
EXECUTABLE = "Reisulkuttab"
APP_ID = "KemalcanYapali.Reisulkuttab"


def asset(name):
    """A bundled asset, from source or a PyInstaller application."""
    root = pathlib.Path(getattr(sys, "_MEIPASS", pathlib.Path(__file__).parent))
    return root / "assets" / name
