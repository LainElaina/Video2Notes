"""Make bundled FFmpeg tools discoverable without relying on the user PATH.

This file runs before the application imports its modules. The sidecar is a
PyInstaller one-directory executable, while the large FFmpeg executables live
beside it in Tauri's immutable resource directory. They are not user data and
do not contain any video, cookies, or model weights.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _prepend_bundled_tools() -> None:
    executable_directory = Path(sys.executable).resolve().parent
    tools_directory = executable_directory / "tools"
    if not tools_directory.is_dir():
        return
    existing = os.environ.get("PATH", "")
    os.environ["PATH"] = str(tools_directory) + (os.pathsep + existing if existing else "")


_prepend_bundled_tools()
