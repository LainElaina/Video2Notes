"""Start the local Vite server, run the Playwright smoke test, and clean up."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def wait_until_ready(url: str, process: subprocess.Popen[bytes], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error = "server did not answer"
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(f"Vite exited before it was ready (exit code {return_code}).")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status < 500:
                    return
        except (OSError, urllib.error.URLError) as error:
            last_error = str(error)
        time.sleep(0.25)
    raise RuntimeError(f"Vite did not become ready within {timeout:.0f}s: {last_error}")


def stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def remove_temporary_directory(path: Path) -> None:
    temporary_base = Path(tempfile.gettempdir()).resolve()
    resolved = path.resolve()
    if (
        resolved.parent != temporary_base
        or not resolved.name.startswith("video2notes-vite-")
    ):
        raise RuntimeError(f"Refusing to remove unsafe temporary path: {resolved}")

    last_error: OSError | None = None
    for _ in range(20):
        try:
            shutil.rmtree(resolved)
            return
        except FileNotFoundError:
            return
        except OSError as error:
            last_error = error
            time.sleep(0.25)
    raise RuntimeError(f"Could not clean temporary Vite directory: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desktop-root", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--smoke-script", required=True, type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:1420")
    parser.add_argument("--ready-timeout", default=60.0, type=float)
    parser.add_argument("--screenshot-dir", type=Path)
    args = parser.parse_args()

    desktop_root = args.desktop_root.resolve(strict=True)
    python_executable = args.python.resolve(strict=True)
    smoke_script = args.smoke_script.resolve(strict=True)
    node = shutil.which("node")
    vite_entry = desktop_root / "node_modules" / "vite" / "bin" / "vite.js"
    if node is None or not vite_entry.is_file():
        raise SystemExit(
            "Node or the locked Vite dependency is unavailable. "
            "Run .\\scripts\\bootstrap.ps1 first."
        )
    server_command = [node, str(vite_entry)]
    creation_flags = (
        subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    )
    temporary_path = Path(tempfile.mkdtemp(prefix="video2notes-vite-"))
    try:
        log_path = temporary_path / "vite.log"
        with log_path.open("wb") as log:
            server = subprocess.Popen(
                server_command,
                cwd=desktop_root,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
                start_new_session=os.name != "nt",
            )
            try:
                wait_until_ready(args.base_url, server, args.ready_timeout)
                smoke_command = [
                    str(python_executable),
                    str(smoke_script),
                    "--base-url",
                    args.base_url,
                ]
                if args.screenshot_dir is not None:
                    smoke_command.extend(
                        ["--screenshot-dir", str(args.screenshot_dir.resolve())]
                    )
                result = subprocess.run(smoke_command, check=False)
                if result.returncode != 0:
                    raise RuntimeError(
                        f"Playwright smoke test exited with code {result.returncode}."
                    )
            except Exception as error:
                log.flush()
                server_log = log_path.read_text(encoding="utf-8", errors="replace")
                if server_log:
                    print("--- Vite output ---", file=sys.stderr)
                    print(server_log, file=sys.stderr)
                raise SystemExit(str(error)) from error
            finally:
                stop_process_tree(server)
    finally:
        remove_temporary_directory(temporary_path)

    print("Managed Playwright smoke completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
