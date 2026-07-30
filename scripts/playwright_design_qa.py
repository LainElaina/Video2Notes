"""Visual QA for the light simple/detailed desktop workspaces."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:1420")
    parser.add_argument("--screenshot-dir", type=Path, required=True)
    args = parser.parse_args()

    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        print("Playwright is not installed.")
        return 2

    screenshot_root = args.screenshot_dir.resolve()
    screenshot_root.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on(
                "console",
                lambda message: (
                    console_errors.append(message.text) if message.type == "error" else None
                ),
            )
            page.goto(f"{args.base_url.rstrip('/')}/?demo=1")
            page.wait_for_load_state("networkidle")
            page.get_by_role("button", name="笔记阅读").click()
            page.get_by_role("button", name="详细视图").click()
            page.get_by_text("EVIDENCE TIMELINE", exact=True).wait_for()
            page.screenshot(
                path=str(screenshot_root / "06-reader-detailed-1440x900.png"),
                full_page=False,
            )

            page.get_by_role("button", name="局部返工").click()
            page.get_by_role("heading", name="局部返工", exact=True).wait_for()
            page.wait_for_timeout(250)
            page.screenshot(
                path=str(screenshot_root / "07-rework-drawer-1440x900.png"),
                full_page=False,
            )
            page.get_by_role("dialog").get_by_role(
                "button", name="关闭局部返工面板"
            ).click()

            page.get_by_role("button", name="补充资料 2").click()
            page.get_by_role("heading", name="补充资料", exact=True).wait_for()
            page.wait_for_timeout(250)
            page.screenshot(
                path=str(screenshot_root / "08-materials-drawer-1440x900.png"),
                full_page=False,
            )
            page.get_by_role("dialog").get_by_role(
                "button", name="关闭补充资料面板"
            ).click()

            page.get_by_role("button", name="生成报告").click()
            page.get_by_role("heading", name="重新生成报告", exact=True).wait_for()
            page.get_by_role("button", name="生成新 revision").click()
            page.get_by_text("历史报告 · 1", exact=True).wait_for()
            page.wait_for_timeout(250)
            page.screenshot(
                path=str(screenshot_root / "09-report-drawer-1440x900.png"),
                full_page=False,
            )
            page.get_by_text("IMMUTABLE HISTORY", exact=True).scroll_into_view_if_needed()
            page.wait_for_timeout(100)
            page.screenshot(
                path=str(screenshot_root / "09b-report-history-1440x900.png"),
                full_page=False,
            )
            page.get_by_role("dialog").get_by_role(
                "button", name="关闭报告重生成面板"
            ).click()

            page.get_by_role("button", name="新建任务", exact=True).click()
            page.locator("details.advanced-options > summary").click()
            page.locator("details.advanced-options").scroll_into_view_if_needed()
            page.wait_for_timeout(150)
            page.screenshot(
                path=str(screenshot_root / "10-create-options-1440x900.png"),
                full_page=False,
            )

            page.get_by_role("button", name="笔记阅读").click()
            page.set_viewport_size({"width": 1180, "height": 760})
            page.screenshot(
                path=str(screenshot_root / "11-reader-detailed-1180x760.png"),
                full_page=False,
            )

            page.set_viewport_size({"width": 720, "height": 860})
            page.screenshot(
                path=str(screenshot_root / "12-reader-detailed-720x860.png"),
                full_page=False,
            )
            browser.close()
    except Error as error:
        print(f"Playwright detailed-workspace QA failed: {error}")
        return 1

    if console_errors:
        print("Browser console errors:")
        for error in console_errors:
            print(f"- {error}")
        return 1
    print("Playwright detailed-workspace QA passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
