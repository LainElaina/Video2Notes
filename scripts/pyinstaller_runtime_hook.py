"""Make bundled native tools and CUDA DLLs discoverable without user setup.

This file runs before the application imports its modules. The sidecar is a
PyInstaller one-directory executable, while the large FFmpeg executables live
beside it in Tauri's immutable resource directory. They are not user data and
do not contain any video, cookies, or model weights.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ``os.add_dll_directory`` returns a handle whose lifetime controls the search
# path.  Keep the handles for the lifetime of the frozen process; relying only
# on PATH is insufficient for every Windows DLL-loading path on Python 3.8+.
_DLL_DIRECTORY_HANDLES: list[object] = []


def _prepend_bundled_tools() -> None:
    executable_directory = Path(sys.executable).resolve().parent
    internal_directory = Path(getattr(sys, "_MEIPASS", executable_directory / "_internal"))
    candidates = [
        executable_directory / "tools",
        internal_directory / "nvidia" / "cublas" / "bin",
        internal_directory / "nvidia" / "cudnn" / "bin",
        internal_directory / "nvidia" / "cuda_nvrtc" / "bin",
    ]
    directories = [item for item in candidates if item.is_dir()]
    if not directories:
        return
    existing = os.environ.get("PATH", "")
    prefix = os.pathsep.join(str(item) for item in directories)
    os.environ["PATH"] = prefix + (os.pathsep + existing if existing else "")

    add_dll_directory = getattr(os, "add_dll_directory", None)
    if callable(add_dll_directory):
        for directory in directories:
            try:
                _DLL_DIRECTORY_HANDLES.append(add_dll_directory(str(directory)))
            except OSError:
                # PATH remains the compatible fallback; one optional search
                # directory must not prevent the sidecar from starting.
                continue


_prepend_bundled_tools()
