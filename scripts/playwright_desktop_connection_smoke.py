"""Verify that the real Tauri WebView reaches the connected backend state."""

from __future__ import annotations

import argparse
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdp-url", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()

    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        print("Playwright is not installed.")
        return 2

    deadline = time.monotonic() + args.timeout_seconds
    last_error = "WebView2 did not expose a page."
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(args.cdp_url)
            page = None
            while time.monotonic() < deadline and page is None:
                pages = [item for context in browser.contexts for item in context.pages]
                page = next(
                    (
                        item
                        for item in pages
                        if not item.url.startswith(("devtools://", "edge://"))
                    ),
                    None,
                )
                if page is None:
                    time.sleep(0.1)
            if page is None:
                print(last_error)
                return 1

            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            page.locator(".topbar-backend.backend-real").wait_for(
                state="visible",
                timeout=remaining_ms,
            )
            status = page.locator(".topbar-backend").inner_text().strip()
            if not status.startswith("后端"):
                print(f"Unexpected backend status: {status!r}")
                return 1
            if page.locator(".topbar-backend.backend-offline").count() != 0:
                print("The desktop UI still exposes an offline backend state.")
                return 1
            if args.screenshot is not None:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(args.screenshot), full_page=False)
    except Error as error:
        print(f"Desktop WebView connection smoke failed: {error}")
        return 1

    print(f"Desktop WebView connected to the packaged backend: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
