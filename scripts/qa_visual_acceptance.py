"""Visual acceptance QA for the Video2Notes desktop UI (visual-refresh branch).

Drives the demo fixture UI across a matrix of viewport sizes, locales, and
workspace modes, captures a screenshot of every page, and asserts the visual
regression contract: no page-level horizontal overflow, no blank pages, no
console errors, working Escape dismissal for drawers and the narrow
slide-over, visible keyboard focus, reduced-motion resilience, and intact
``title`` tooltips on truncated paths.

Usage:
    python scripts/qa_visual_acceptance.py
    python scripts/qa_visual_acceptance.py --sizes 1440x900,820x900 --locales zh --modes guided

Requirements:
    pip install playwright && playwright install chromium
    The Vite dev server must be running (default http://127.0.0.1:1420);
    start it with `pnpm dev` in apps/desktop.

Exit code is 1 when any capture or check fails, 0 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

LOCALE_VALUES = {"zh": "zh-CN", "en": "en-US"}
MODE_NAMES = {
    "zh": {"guided": "简约视图", "professional": "数据工作室"},
    "en": {"guided": "Guided view", "professional": "Data studio"},
}
PAGE_NAMES = {
    "zh": {
        "create": "新建任务",
        "tasks": "任务运行",
        "reader": "笔记阅读",
        "settings": "设置",
    },
    "en": {
        "create": "New task",
        "tasks": "Runs",
        "reader": "Reader",
        "settings": "Settings",
    },
}
PAGE_SELECTORS = {
    "create": ".create-page",
    "tasks": ".run-page",
    "reader": ".reader-page",
    "settings": ".models-page",
}
PAGE_ORDER = ("create", "tasks", "reader", "settings")

APP_READY_TIMEOUT_MS = 15_000
SETTLE_MS = 350


class Reporter:
    """Collects check results and prints one JSON line per result."""

    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def record(self, kind: str, name: str, ok: bool, **details: Any) -> None:
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        line = {"type": kind, "name": name, "ok": ok, **details}
        print(json.dumps(line, ensure_ascii=False), flush=True)

    def check(self, name: str, condition: bool, **details: Any) -> bool:
        self.record("check", name, condition, **details)
        return condition

    def summary(self, **details: Any) -> None:
        line = {
            "type": "summary",
            "passed": self.passed,
            "failed": self.failed,
            **details,
        }
        print(json.dumps(line, ensure_ascii=False), flush=True)


def parse_sizes(value: str) -> list[tuple[int, int]]:
    sizes: list[tuple[int, int]] = []
    for chunk in value.split(","):
        chunk = chunk.strip().lower()
        if not chunk:
            continue
        width, _, height = chunk.partition("x")
        sizes.append((int(width), int(height)))
    if not sizes:
        raise ValueError(f"no valid sizes in {value!r}")
    return sizes


def parse_tokens(value: str, allowed: set[str], label: str) -> list[str]:
    tokens = [token.strip() for token in value.split(",") if token.strip()]
    unknown = [token for token in tokens if token not in allowed]
    if unknown:
        raise ValueError(f"unknown {label}: {', '.join(unknown)}")
    if not tokens:
        raise ValueError(f"no valid {label} in {value!r}")
    return tokens


def wait_for_demo_backend(page: Any) -> None:
    page.wait_for_selector(".app-shell", timeout=APP_READY_TIMEOUT_MS)
    # Demo initialization replaces the store (resetting view and panel state),
    # so interactions must wait for it to finish.
    page.wait_for_selector(
        ".topbar-backend.backend-demo",
        timeout=APP_READY_TIMEOUT_MS,
    )


def open_app(browser: Any, base_url: str, width: int, height: int, reduced_motion: bool = False):
    context = browser.new_context(
        viewport={"width": width, "height": height},
        reduced_motion="reduce" if reduced_motion else "no-preference",
    )
    page = context.new_page()
    console_errors: list[str] = []
    page_errors: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text)
        if message.type == "error"
        else None,
    )
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.goto(base_url, wait_until="domcontentloaded")
    wait_for_demo_backend(page)
    return context, page, console_errors, page_errors


def set_locale(page: Any, locale: str) -> None:
    page.locator("label.locale-control select").select_option(LOCALE_VALUES[locale])
    page.wait_for_function(
        "lang => document.documentElement.lang === lang",
        arg=LOCALE_VALUES[locale],
    )


def set_mode(page: Any, reporter: Reporter, locale: str, mode: str, label: str) -> None:
    page.get_by_role(
        "button", name=MODE_NAMES[locale][mode], exact=True
    ).click()
    page.wait_for_function(
        "mode => document.querySelector('.app-shell')?.dataset.workspaceMode === mode",
        arg=mode,
    )
    reporter.check(f"{label} workspace-mode={mode}", True)


def visit_page(page: Any, locale: str, page_id: str) -> None:
    page.get_by_role(
        "button", name=PAGE_NAMES[locale][page_id], exact=True
    ).click()
    page.wait_for_selector(PAGE_SELECTORS[page_id], timeout=APP_READY_TIMEOUT_MS)
    page.wait_for_timeout(SETTLE_MS)


def check_page_invariants(page: Any, reporter: Reporter, label: str) -> None:
    scroll_width, inner_width = page.evaluate(
        "() => [document.documentElement.scrollWidth, window.innerWidth]"
    )
    reporter.check(
        f"{label} no horizontal overflow",
        scroll_width <= inner_width,
        scrollWidth=scroll_width,
        innerWidth=inner_width,
    )
    text_length = page.evaluate("() => document.body.innerText.trim().length")
    reporter.check(
        f"{label} page not blank",
        text_length >= 20,
        textLength=text_length,
    )


def run_matrix(
    browser: Any,
    reporter: Reporter,
    base_url: str,
    out_dir: Path,
    sizes: list[tuple[int, int]],
    locales: list[str],
    modes: list[str],
) -> None:
    for width, height in sizes:
        for locale in locales:
            for mode in modes:
                combo = f"{width}x{height}/{locale}/{mode}"
                context, page, console_errors, page_errors = open_app(
                    browser, base_url, width, height
                )
                try:
                    set_locale(page, locale)
                    set_mode(page, reporter, locale, mode, combo)
                    for page_id in PAGE_ORDER:
                        page_label = f"{combo}/{page_id}"
                        visit_page(page, locale, page_id)
                        screenshot = out_dir / f"{width}x{height}-{locale}-{mode}-{page_id}.png"
                        page.screenshot(path=str(screenshot), full_page=False)
                        reporter.record(
                            "capture",
                            page_label,
                            True,
                            screenshot=str(screenshot),
                        )
                        check_page_invariants(page, reporter, page_label)
                except Exception as error:  # noqa: BLE001 - record and keep going
                    reporter.record("capture", combo, False, error=str(error))
                finally:
                    reporter.check(
                        f"{combo} zero console errors",
                        not console_errors,
                        consoleErrors=console_errors,
                    )
                    reporter.check(
                        f"{combo} zero page errors",
                        not page_errors,
                        pageErrors=page_errors,
                    )
                    context.close()


def check_drawer_escape(page: Any, reporter: Reporter) -> None:
    visit_page(page, "zh", "reader")
    page.get_by_role("button", name="局部返工", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.wait_for(timeout=APP_READY_TIMEOUT_MS)
    reporter.check("drawer opens from reader", dialog.count() == 1)
    page.keyboard.press("Escape")
    gone = False
    try:
        page.get_by_role("dialog").wait_for(state="detached", timeout=3_000)
        gone = True
    except Exception:  # noqa: BLE001 - reported via the check result
        gone = page.get_by_role("dialog").count() == 0
    reporter.check("Escape closes the open drawer", gone)


def check_narrow_slide_over(browser: Any, reporter: Reporter, base_url: str) -> None:
    context, page, console_errors, page_errors = open_app(browser, base_url, 820, 900)
    try:
        expand = page.get_by_role("button", name="展开侧栏", exact=True)
        expand.wait_for(timeout=APP_READY_TIMEOUT_MS)
        reporter.check("narrow entry auto-collapses the panel", True)
        expand.click()
        scrim = page.locator(".context-scrim")
        scrim.wait_for(timeout=APP_READY_TIMEOUT_MS)
        reporter.check("slide-over opens with a scrim", scrim.count() == 1)
        page.keyboard.press("Escape")
        gone = False
        try:
            scrim.wait_for(state="detached", timeout=3_000)
            gone = True
        except Exception:  # noqa: BLE001 - reported via the check result
            gone = scrim.count() == 0
        reporter.check("Escape closes the narrow slide-over", gone)
        reporter.check(
            "narrow slide-over zero console errors",
            not console_errors,
            consoleErrors=console_errors,
        )
        reporter.check(
            "narrow slide-over zero page errors",
            not page_errors,
            pageErrors=page_errors,
        )
    except Exception as error:  # noqa: BLE001 - record and keep going
        reporter.record("check", "narrow slide-over", False, error=str(error))
    finally:
        context.close()


def check_keyboard_focus(page: Any, reporter: Reporter) -> None:
    visit_page(page, "zh", "create")
    page.locator(".topbar-brand").focus()
    for step in range(1, 5):
        page.keyboard.press("Tab")
        state = page.evaluate(
            """
            () => {
              const element = document.activeElement
              if (!element || element === document.body) {
                return { tag: 'body', width: 0, height: 0, visible: false }
              }
              const rect = element.getBoundingClientRect()
              const style = getComputedStyle(element)
              return {
                tag: element.tagName.toLowerCase(),
                text: (element.textContent || '').trim().slice(0, 24),
                width: rect.width,
                height: rect.height,
                visible: style.visibility !== 'hidden' && style.display !== 'none',
              }
            }
            """
        )
        reporter.check(
            f"Tab stop {step} lands on a visible focusable element",
            state["visible"] and state["width"] > 0 and state["height"] > 0,
            activeElement=state,
        )


def check_reduced_motion(
    browser: Any,
    reporter: Reporter,
    base_url: str,
) -> None:
    context, page, console_errors, page_errors = open_app(
        browser, base_url, 1440, 900, reduced_motion=True
    )
    try:
        reduced = page.evaluate(
            "() => matchMedia('(prefers-reduced-motion: reduce)').matches"
        )
        reporter.check("reduced-motion media query active", reduced)
        for page_id in PAGE_ORDER:
            visit_page(page, "zh", page_id)
            check_page_invariants(page, reporter, f"reduced-motion/{page_id}")
        reporter.check(
            "reduced-motion zero console errors",
            not console_errors,
            consoleErrors=console_errors,
        )
        reporter.check(
            "reduced-motion zero page errors",
            not page_errors,
            pageErrors=page_errors,
        )
    except Exception as error:  # noqa: BLE001 - record and keep going
        reporter.record("check", "reduced-motion navigation", False, error=str(error))
    finally:
        context.close()


def check_truncated_path_titles(page: Any, reporter: Reporter) -> None:
    visit_page(page, "zh", "settings")
    elements = page.evaluate(
        """
        () => Array.from(document.querySelectorAll('.truncate-start')).map(element => ({
          hasTitle: element.hasAttribute('title'),
          title: element.getAttribute('title'),
        }))
        """
    )
    titled = [item for item in elements if item["hasTitle"]]
    reporter.check(
        ".truncate-start elements exist on the settings page",
        len(elements) > 0,
        count=len(elements),
    )
    reporter.check(
        ".truncate-start path elements keep a non-empty title",
        len(titled) > 0 and all(item["title"] for item in titled),
        titledCount=len(titled),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:1420/?demo=1",
        help="fixture URL of the running dev server (default: %(default)s)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "artifacts" / "qa-screenshots",
        help="screenshot output directory (default: %(default)s)",
    )
    parser.add_argument(
        "--sizes",
        default="1440x900,1120x800,820x900,640x900",
        help="comma-separated WxH viewports (default: %(default)s)",
    )
    parser.add_argument(
        "--locales",
        default="zh,en",
        help="comma-separated locale tokens (zh, en) (default: %(default)s)",
    )
    parser.add_argument(
        "--modes",
        default="guided,professional",
        help="comma-separated workspace modes (guided, professional) (default: %(default)s)",
    )
    args = parser.parse_args()

    try:
        sizes = parse_sizes(args.sizes)
        locales = parse_tokens(args.locales, set(LOCALE_VALUES), "locales")
        modes = parse_tokens(
            args.modes, {"guided", "professional"}, "modes"
        )
    except ValueError as error:
        print(f"Invalid arguments: {error}", file=sys.stderr)
        return 2

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "Playwright is not installed. Run "
            "`pip install playwright && playwright install chromium`.",
            file=sys.stderr,
        )
        return 2

    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    reporter = Reporter()
    started = time.monotonic()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            run_matrix(
                browser, reporter, args.base_url, out_dir, sizes, locales, modes
            )

            # Interaction checks run once at 1440x900 zh guided.
            context, page, console_errors, page_errors = open_app(
                browser, args.base_url, 1440, 900
            )
            try:
                check_drawer_escape(page, reporter)
                check_keyboard_focus(page, reporter)
                check_truncated_path_titles(page, reporter)
                reporter.check(
                    "interaction zero console errors",
                    not console_errors,
                    consoleErrors=console_errors,
                )
                reporter.check(
                    "interaction zero page errors",
                    not page_errors,
                    pageErrors=page_errors,
                )
            except Exception as error:  # noqa: BLE001 - record and keep going
                reporter.record("check", "interaction checks", False, error=str(error))
            finally:
                context.close()

            check_narrow_slide_over(browser, reporter, args.base_url)
            check_reduced_motion(browser, reporter, args.base_url)
        finally:
            browser.close()

    reporter.summary(
        seconds=round(time.monotonic() - started, 2),
        screenshots=str(out_dir),
    )
    return 1 if reporter.failed else 0


if __name__ == "__main__":
    sys.exit(main())
