"""Exercise the React workbench against a running real loopback API."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:1420")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--screenshot-dir", type=Path, required=True)
    parser.add_argument("--timeout-ms", type=int, default=90_000)
    parser.add_argument(
        "--mode",
        choices=("fast", "balanced", "accurate"),
        default="fast",
    )
    parser.add_argument(
        "--pdf",
        action="store_true",
        help="request and verify the deterministic PDF export",
    )
    args = parser.parse_args()
    source = args.source.resolve()
    if not source.is_file():
        raise SystemExit(f"Real UI smoke source does not exist: {source}")
    args.screenshot_dir.mkdir(parents=True, exist_ok=True)

    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError as error:
        raise SystemExit("Playwright is not installed.") from error

    console_errors: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on(
                "console",
                lambda message: console_errors.append(message.text)
                if message.type == "error"
                else None,
            )
            page.goto(args.base_url, wait_until="networkidle")
            page.get_by_text("后端 0.2.0", exact=True).wait_for(timeout=15_000)
            page.get_by_role("button", name="新建任务", exact=True).click()
            # The processing profile is part of the source probe policy because it
            # can change the exact media format we require.  Select it before the
            # probe so the verified manifest is current when the job is submitted.
            page.locator(f"input[type='radio'][value='{args.mode}']").check(force=True)
            source_input = page.get_by_label("视频链接")
            source_input.fill(str(source))
            page.get_by_role("button", name="探测来源").click()
            page.get_by_text("SOURCE VERIFIED", exact=True).wait_for(timeout=15_000)
            page.get_by_role("button", name="高级选项").click()
            page.locator(".advanced-options-content").wait_for()
            pdf_toggle = page.get_by_label("同时生成离线 PDF")
            if args.pdf:
                pdf_toggle.check()
            else:
                pdf_toggle.uncheck()
            page.screenshot(
                path=str(args.screenshot_dir / "06-real-probed-1440x900.png"),
                full_page=True,
            )
            page.get_by_role("button", name="开始处理").click()
            page.get_by_role("button", name="取消").wait_for(timeout=10_000)
            page.get_by_role("button", name="阅读笔记").wait_for(timeout=args.timeout_ms)
            page.screenshot(
                path=str(args.screenshot_dir / "07-real-completed-1440x900.png"),
                full_page=True,
            )
            page.get_by_role("button", name="阅读笔记").click()
            page.locator("article.note-paper").wait_for(timeout=15_000)
            page.locator("video.local-video").wait_for(timeout=15_000)
            if args.mode != "fast":
                # Fast deliberately has a zero screenshot budget.  The higher
                # profiles must still prove that selected frames reach the reader.
                page.locator("img.frame-image").first.wait_for(timeout=15_000)
            page.screenshot(
                path=str(args.screenshot_dir / "08-real-reader-1440x900.png"),
                full_page=True,
            )
            page.get_by_role("button", name="导出").click()
            page.get_by_role("button", name="Markdown", exact=True).wait_for()
            if args.pdf:
                page.get_by_role("button", name="打印 / PDF", exact=True).wait_for()
            browser.close()
    except Error as error:
        raise SystemExit(f"Real API Playwright smoke failed: {error}") from error

    if console_errors:
        raise SystemExit("Browser console errors: " + " | ".join(console_errors))
    print("Real API Playwright smoke passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
