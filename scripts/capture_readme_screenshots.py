"""Capture the current demo UI and encode README screenshots as compact WebP files."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from PIL import Image
from playwright.sync_api import Error, Page, sync_playwright

VIEWPORT = {"width": 1600, "height": 1000}


def encode_webp(png_path: Path, output_path: Path) -> None:
    with Image.open(png_path) as image:
        image.convert("RGB").save(
            output_path,
            "WEBP",
            quality=90,
            method=6,
        )


def capture(page: Page, temporary_root: Path, output_root: Path, name: str) -> None:
    page.wait_for_timeout(280)
    png_path = temporary_root / f"{name}.png"
    page.screenshot(path=str(png_path), full_page=False)
    encode_webp(png_path, output_root / f"{name}.webp")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:1420")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/assets/readme"),
    )
    args = parser.parse_args()

    output_root = args.output_dir.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []

    try:
        with tempfile.TemporaryDirectory(prefix="video2notes-readme-") as directory:
            temporary_root = Path(directory)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport=VIEWPORT)
                page.on(
                    "console",
                    lambda message: (
                        console_errors.append(message.text)
                        if message.type == "error"
                        else None
                    ),
                )
                page.goto(f"{args.base_url.rstrip('/')}?demo=1", wait_until="networkidle")

                capture(page, temporary_root, output_root, "create-task")

                page.get_by_label("视频链接").fill(
                    "https://www.bilibili.com/video/BV1EvidenceDemo"
                )
                page.get_by_role("button", name="探测来源").click()
                page.locator("input[type='radio'][value='accurate']").check(force=True)
                page.get_by_role("button", name="开始处理").click()
                page.get_by_role("button", name="取消").wait_for(timeout=5_000)
                page.get_by_role("heading", name="证据轨正在形成").wait_for(timeout=5_000)
                capture(page, temporary_root, output_root, "analysis-workspace")

                page.get_by_role("button", name="笔记阅读").click()
                page.get_by_role(
                    "heading",
                    name="从视频到可追溯知识：统一证据时间轴",
                    level=1,
                ).wait_for()
                capture(page, temporary_root, output_root, "note-reader")

                page.get_by_role("button", name="模型设置").click()
                page.get_by_role("heading", name="模型与角色路由").wait_for()
                page.get_by_role("heading", name="依赖与运行时").wait_for()
                notice_close = page.locator(".notice-bar button")
                if notice_close.is_visible():
                    notice_close.click()
                capture(page, temporary_root, output_root, "runtime-manager")

                page.get_by_role("button", name="自定义性能").click()
                performance_heading = page.get_by_role(
                    "heading", name="这台电脑应该如何工作"
                )
                performance_heading.wait_for()
                page.locator(".performance-panel").evaluate(
                    "element => element.scrollIntoView({ block: 'start' })"
                )
                capture(page, temporary_root, output_root, "performance-settings")

                provider_heading = page.get_by_role(
                    "heading", name="协议、认证与模型目录"
                )
                provider_heading.wait_for()
                page.locator(".provider-workbench").evaluate(
                    "element => element.scrollIntoView({ block: 'start' })"
                )
                page.get_by_role("button", name="新增供应商").click()
                page.get_by_label("供应商编辑器", exact=True).wait_for()
                capture(page, temporary_root, output_root, "model-protocols")
                browser.close()
    except Error as error:
        print(f"README screenshot capture failed: {error}")
        return 1

    if console_errors:
        print("Browser console errors:")
        for error in console_errors:
            print(f"- {error}")
        return 1

    print(f"README screenshots written to {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
