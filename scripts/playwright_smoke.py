"""No-network primary-path smoke test for the fixture-backed desktop workbench."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:1420")
    parser.add_argument(
        "--screenshot-dir",
        type=Path,
        help="optional persistent directory for visual-QA screenshots",
    )
    args = parser.parse_args()

    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError as error:
        raise SystemExit(
            "Playwright is not installed. Run .\\scripts\\bootstrap.ps1 -WithPlaywright."
        ) from error

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            console_errors: list[str] = []
            page.on(
                "console",
                lambda message: console_errors.append(message.text)
                if message.type == "error"
                else None,
            )
            page.goto(f"{args.base_url.rstrip('/')}?demo=1", wait_until="networkidle")
            screenshot_root = args.screenshot_dir
            if screenshot_root is not None:
                screenshot_root.mkdir(parents=True, exist_ok=True)
                page.screenshot(
                    path=str(screenshot_root / "01-create-1440x900.png"),
                    full_page=True,
                )
            page.get_by_label("视频链接").fill("https://www.bilibili.com/video/BV1EvidenceDemo")
            page.get_by_role("button", name="探测来源").click()
            page.locator("input[type='radio'][value='accurate']").check(force=True)
            page.get_by_role("button", name="开始处理").click()
            page.get_by_role("button", name="取消").wait_for(timeout=5_000)
            page.get_by_role("heading", name="证据轨正在形成").wait_for(timeout=5_000)
            if screenshot_root is not None:
                page.screenshot(
                    path=str(screenshot_root / "02-run-1440x900.png"),
                    full_page=True,
                )
                page.get_by_role("button", name="笔记阅读").click()
                page.get_by_role(
                    "heading",
                    name="从视频到可追溯知识：统一证据时间轴",
                    level=1,
                ).wait_for()
                page.screenshot(
                    path=str(screenshot_root / "03-reader-1440x900.png"),
                    full_page=True,
                )
                page.get_by_role("button", name="模型设置").click()
                page.get_by_role("heading", name="模型与角色路由").wait_for()
                page.screenshot(
                    path=str(screenshot_root / "04-models-1440x900.png"),
                    full_page=True,
                )
                page.set_viewport_size({"width": 1180, "height": 760})
                page.get_by_role("button", name="新建任务", exact=True).click()
                page.screenshot(
                    path=str(screenshot_root / "05-create-1180x760.png"),
                    full_page=True,
                )
            else:
                with tempfile.TemporaryDirectory(prefix="video2notes-playwright-") as directory:
                    page.screenshot(path=str(Path(directory) / "primary-path.png"), full_page=True)
            browser.close()
    except Error as error:
        raise SystemExit(f"Playwright primary-path smoke test failed: {error}") from error

    if console_errors:
        raise SystemExit("Browser console errors: " + " | ".join(console_errors))
    print("Playwright primary-path smoke test passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
