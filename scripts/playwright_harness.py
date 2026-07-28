"""Start the local Vite server, run the Playwright smoke test, and clean up."""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def wait_until_ready(
    url: str,
    process: subprocess.Popen[bytes],
    timeout: float,
    process_name: str,
) -> None:
    deadline = time.monotonic() + timeout
    last_error = "server did not answer"
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(
                f"{process_name} exited before it was ready (exit code {return_code})."
            )
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status < 500:
                    return
        except (OSError, urllib.error.URLError) as error:
            last_error = str(error)
        time.sleep(0.25)
    raise RuntimeError(
        f"{process_name} did not become ready within {timeout:.0f}s: {last_error}"
    )


def find_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


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
    parser.add_argument("--real-smoke-script", type=Path)
    parser.add_argument("--real-source", type=Path)
    args = parser.parse_args()

    desktop_root = args.desktop_root.resolve(strict=True)
    python_executable = args.python.resolve(strict=True)
    smoke_script = args.smoke_script.resolve(strict=True)
    real_smoke_script = (
        args.real_smoke_script.resolve(strict=True)
        if args.real_smoke_script is not None
        else None
    )
    real_source = (
        args.real_source.resolve(strict=True) if args.real_source is not None else None
    )
    if (real_smoke_script is None) != (real_source is None):
        raise SystemExit("--real-smoke-script and --real-source must be used together.")
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
        vite_log_path = temporary_path / "vite.log"
        api_log_path = temporary_path / "api.log"
        with (
            vite_log_path.open("wb") as vite_log,
            api_log_path.open("wb") as api_log,
        ):
            api_process: subprocess.Popen[bytes] | None = None
            vite_process: subprocess.Popen[bytes] | None = None
            vite_environment = os.environ.copy()
            try:
                if real_smoke_script is not None and real_source is not None:
                    api_port = find_loopback_port()
                    api_token = secrets.token_urlsafe(32)
                    api_data_root = temporary_path / "real api data"
                    api_data_root.mkdir()
                    api_environment = os.environ.copy()
                    api_environment["VIDEO2NOTES_TOKEN"] = api_token
                    api_process = subprocess.Popen(
                        [
                            str(python_executable),
                            "-m",
                            "video2notes",
                            "serve",
                            "--port",
                            str(api_port),
                            "--data-root",
                            str(api_data_root),
                        ],
                        cwd=desktop_root.parent.parent,
                        stdout=api_log,
                        stderr=subprocess.STDOUT,
                        env=api_environment,
                        creationflags=creation_flags,
                        start_new_session=os.name != "nt",
                    )
                    wait_until_ready(
                        f"http://127.0.0.1:{api_port}/api/health",
                        api_process,
                        args.ready_timeout,
                        "Video2Notes API",
                    )
                    vite_environment["VITE_VIDEO2NOTES_API_URL"] = (
                        f"http://127.0.0.1:{api_port}"
                    )
                    vite_environment["VITE_VIDEO2NOTES_API_TOKEN"] = api_token

                vite_process = subprocess.Popen(
                    server_command,
                    cwd=desktop_root,
                    stdout=vite_log,
                    stderr=subprocess.STDOUT,
                    env=vite_environment,
                    creationflags=creation_flags,
                    start_new_session=os.name != "nt",
                )
                wait_until_ready(
                    args.base_url,
                    vite_process,
                    args.ready_timeout,
                    "Vite",
                )
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
                if real_smoke_script is not None and real_source is not None:
                    real_command = [
                        str(python_executable),
                        str(real_smoke_script),
                        "--base-url",
                        args.base_url,
                        "--source",
                        str(real_source),
                        "--screenshot-dir",
                        str(temporary_path / "real screenshots"),
                        "--mode",
                        "fast",
                    ]
                    real_result = subprocess.run(real_command, check=False)
                    if real_result.returncode != 0:
                        raise RuntimeError(
                            "Real API Playwright smoke test exited with code "
                            f"{real_result.returncode}."
                        )
            except Exception as error:
                vite_log.flush()
                api_log.flush()
                vite_output = vite_log_path.read_text(encoding="utf-8", errors="replace")
                api_output = api_log_path.read_text(encoding="utf-8", errors="replace")
                if vite_output:
                    print("--- Vite output ---", file=sys.stderr)
                    print(vite_output, file=sys.stderr)
                if api_output:
                    print("--- Video2Notes API output ---", file=sys.stderr)
                    print(api_output, file=sys.stderr)
                raise SystemExit(str(error)) from error
            finally:
                if vite_process is not None:
                    stop_process_tree(vite_process)
                if api_process is not None:
                    stop_process_tree(api_process)
    finally:
        remove_temporary_directory(temporary_path)

    print("Managed demo and real-API Playwright smoke completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
