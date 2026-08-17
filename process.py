"""Platform-specific subprocess options."""

import os
import subprocess


def windowless_options():
    """Prevent Windows console children from opening a terminal window."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}
