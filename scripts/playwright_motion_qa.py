"""Interaction and motion-regression QA for the fixture-backed desktop UI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


VIEWPORTS = (
    (1440, 900),
    (1180, 760),
    (720, 860),
)
REDUCED_ANIMATION_MAX_MS = 20
REDUCED_TRANSITION_MAX_MS = 150


def _fixture_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/?demo=1"


def _attach_browser_diagnostics(
    page: Any,
    label: str,
    console_errors: list[str],
    page_errors: list[str],
) -> None:
    page.on(
        "console",
        lambda message: (
            console_errors.append(f"{label}: {message.text}")
            if message.type == "error"
            else None
        ),
    )
    page.on("pageerror", lambda error: page_errors.append(f"{label}: {error}"))


def _open_reader(page: Any, url: str, expect: Any) -> None:
    page.goto(url, wait_until="networkidle")
    page.get_by_role("button", name="笔记阅读").click()
    expect(page.locator(".reader-page")).to_be_visible()


def _wait_for_finite_motion(page: Any, timeout: int = 3_000) -> None:
    page.wait_for_function(
        """
        () => document.getAnimations().every(animation => {
          if (animation.playState !== 'running') return true
          const timing = animation.effect?.getComputedTiming()
          return timing ? !Number.isFinite(timing.endTime) : true
        })
        """,
        timeout=timeout,
    )


def _assert_single_reader_workspace(page: Any, expect: Any, mode: str) -> None:
    app_shell = page.locator(".app-shell")
    guided = page.get_by_role("button", name="简约视图")
    professional = page.get_by_role("button", name="数据工作室")

    expect(app_shell).to_have_attribute("data-workspace-mode", mode)
    if mode == "professional":
        expect(professional).to_have_attribute("aria-pressed", "true")
        expect(guided).to_have_attribute("aria-pressed", "false")
        expect(page.locator(".detailed-evidence-studio")).to_have_count(1)
        expect(page.locator(".reader-split")).to_have_count(0)
    else:
        expect(guided).to_have_attribute("aria-pressed", "true")
        expect(professional).to_have_attribute("aria-pressed", "false")
        expect(page.locator(".reader-split")).to_have_count(1)
        expect(page.locator(".detailed-evidence-studio")).to_have_count(0)


def _exercise_rapid_workspace_switching(page: Any, expect: Any) -> None:
    guided = page.get_by_role("button", name="简约视图")
    professional = page.get_by_role("button", name="数据工作室")

    for target in (professional, guided, professional, guided, professional):
        target.click()
    _wait_for_finite_motion(page)
    _assert_single_reader_workspace(page, expect, "professional")

    for target in (guided, professional, guided):
        target.click()
    _wait_for_finite_motion(page)
    _assert_single_reader_workspace(page, expect, "guided")


def _exercise_export_menu(page: Any, expect: Any) -> None:
    export_button = page.get_by_role("button", name="导出", exact=True)
    menu = page.locator(".export-menu")

    export_button.click()
    expect(export_button).to_have_attribute("aria-expanded", "true")
    expect(menu).to_have_count(1)

    export_button.click()
    expect(export_button).to_have_attribute("aria-expanded", "false")
    expect(menu).to_have_count(0, timeout=2_000)

    export_button.click()
    expect(export_button).to_have_attribute("aria-expanded", "true")
    expect(page.get_by_role("button", name="Markdown", exact=True)).to_be_visible()
    page.locator(".reader-search input").click()
    expect(export_button).to_have_attribute("aria-expanded", "false")
    expect(menu).to_have_count(0, timeout=2_000)


def _exercise_drawers(page: Any, expect: Any) -> None:
    drawers = (
        ("局部返工", "关闭局部返工面板"),
        ("补充资料 2", "关闭补充资料面板"),
        ("生成报告", "关闭报告重生成面板"),
    )

    for opener_name, closer_name in drawers:
        opener = page.get_by_role("button", name=opener_name, exact=True)
        opener.click()
        expect(page.get_by_role("dialog")).to_have_count(1)
        expect(page.locator(".workbench-overlay")).to_have_count(1)

        page.get_by_role("button", name=closer_name, exact=True).last.click()
        opener.dispatch_event("click")
        expect(page.locator(".motion-presence-overlay")).to_have_attribute(
            "data-motion-state",
            "entered",
        )
        expect(page.get_by_role("dialog")).to_have_count(1)
        expect(page.locator(".workbench-overlay")).to_have_count(1)

        page.keyboard.press("Escape")
        expect(page.locator(".workbench-overlay")).to_have_count(0, timeout=2_000)
        expect(page.locator(".motion-presence-overlay")).to_have_count(0)


def _css_motion_style(page: Any, selector: str) -> dict[str, Any]:
    return page.locator(selector).evaluate(
        """
        element => {
          const toMilliseconds = value => {
            const trimmed = value.trim()
            if (trimmed.endsWith('ms')) return Number.parseFloat(trimmed) || 0
            if (trimmed.endsWith('s')) return (Number.parseFloat(trimmed) || 0) * 1000
            return 0
          }
          const maximum = value => Math.max(
            0,
            ...value.split(',').map(toMilliseconds),
          )
          const style = getComputedStyle(element)
          return {
            animationMs: maximum(style.animationDuration),
            transitionMs: maximum(style.transitionDuration),
            transform: style.transform,
          }
        }
        """
    )


def _assert_reduced_motion_style(page: Any, selector: str) -> None:
    style = _css_motion_style(page, selector)
    assert style["animationMs"] <= REDUCED_ANIMATION_MAX_MS, (
        f"{selector} retained a {style['animationMs']:.3f}ms animation "
        "under prefers-reduced-motion."
    )
    assert style["transitionMs"] <= REDUCED_TRANSITION_MAX_MS, (
        f"{selector} retained a {style['transitionMs']:.3f}ms transition "
        "under prefers-reduced-motion."
    )
    assert style["transform"] == "none", (
        f"{selector} retained transform {style['transform']!r} "
        "under prefers-reduced-motion."
    )


def _assert_reduced_motion(page: Any, expect: Any) -> None:
    assert page.evaluate(
        "matchMedia('(prefers-reduced-motion: reduce)').matches"
    ), "The reduced-motion media query was not active."

    page.get_by_role("button", name="数据工作室").click()
    expect(page.locator(".detailed-evidence-studio")).to_be_visible()

    page.get_by_role("button", name="导出", exact=True).click()
    expect(page.locator(".export-menu")).to_be_visible()
    for selector in (".motion-presence-popover", ".export-menu"):
        _assert_reduced_motion_style(page, selector)
    page.get_by_role("button", name="局部返工", exact=True).click()
    expect(page.get_by_role("dialog")).to_be_visible()

    selectors = (
        ".reader-mode-surface",
        ".motion-presence-overlay",
        ".workbench-scrim",
        ".workbench-drawer",
    )
    for selector in selectors:
        _assert_reduced_motion_style(page, selector)

    page.keyboard.press("Escape")
    expect(page.locator(".workbench-overlay")).to_have_count(0, timeout=2_000)


def _assert_viewport_bounds(page: Any, label: str) -> None:
    failures = page.evaluate(
        """
        () => {
          const tolerance = 1
          const failures = []
          const root = document.documentElement
          const body = document.body
          if (root.scrollWidth > root.clientWidth + tolerance) {
            failures.push(
              `document: scrollWidth ${root.scrollWidth} > clientWidth ${root.clientWidth}`,
            )
          }
          if (body.scrollWidth > root.clientWidth + tolerance) {
            failures.push(
              `body: scrollWidth ${body.scrollWidth} > viewport ${root.clientWidth}`,
            )
          }

          const selectors = [
            '.top-navigation',
            '.workspace',
            '.workspace-main',
            '.reader-page',
            '.reader-mode-surface',
            '.detailed-evidence-studio',
            '.export-menu',
            '.workbench-overlay',
            '.workbench-drawer',
          ]
          for (const selector of selectors) {
            document.querySelectorAll(selector).forEach((element, index) => {
              const rect = element.getBoundingClientRect()
              if (rect.width === 0 || rect.height === 0) return
              if (rect.left < -tolerance || rect.right > innerWidth + tolerance) {
                failures.push(
                  `${selector}[${index}]: horizontal bounds ${rect.left.toFixed(1)}..` +
                  `${rect.right.toFixed(1)} outside 0..${innerWidth}`,
                )
              }
            })
          }
          return failures
        }
        """
    )
    assert not failures, f"{label} overflow:\n- " + "\n- ".join(failures)


def _exercise_viewport_overflow(
    page: Any,
    expect: Any,
    screenshot_root: Path | None,
    width: int,
    height: int,
) -> None:
    label = f"{width}x{height}"
    page.get_by_role("button", name="简约视图").click()
    _wait_for_finite_motion(page)
    _assert_viewport_bounds(page, f"{label} guided workspace")

    page.get_by_role("button", name="数据工作室").click()
    expect(page.locator(".detailed-evidence-studio")).to_be_visible()
    _wait_for_finite_motion(page)
    _assert_viewport_bounds(page, f"{label} professional workspace")

    page.get_by_role("button", name="导出", exact=True).click()
    expect(page.locator(".export-menu")).to_be_visible()
    _wait_for_finite_motion(page)
    _assert_viewport_bounds(page, f"{label} export menu")
    page.keyboard.press("Escape")
    expect(page.locator(".export-menu")).to_have_count(0, timeout=2_000)

    page.get_by_role("button", name="局部返工", exact=True).click()
    expect(page.get_by_role("dialog")).to_be_visible()
    _wait_for_finite_motion(page)
    _assert_viewport_bounds(page, f"{label} rework drawer")

    if screenshot_root is not None:
        page.screenshot(
            path=str(screenshot_root / f"motion-qa-{label}.png"),
            full_page=False,
        )

    page.keyboard.press("Escape")
    expect(page.locator(".workbench-overlay")).to_have_count(0, timeout=2_000)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:1420")
    parser.add_argument(
        "--screenshot-dir",
        type=Path,
        help="optional directory for one drawer screenshot per tested viewport",
    )
    args = parser.parse_args()

    try:
        from playwright.sync_api import Error, expect, sync_playwright
    except ImportError as error:
        raise SystemExit(
            "Playwright is not installed. Run .\\scripts\\bootstrap.ps1 -WithPlaywright."
        ) from error

    screenshot_root = args.screenshot_dir.resolve() if args.screenshot_dir else None
    if screenshot_root is not None:
        screenshot_root.mkdir(parents=True, exist_ok=True)

    console_errors: list[str] = []
    page_errors: list[str] = []
    fixture_url = _fixture_url(args.base_url)

    failure: str | None = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)

            interaction_page = browser.new_page(viewport={"width": 1440, "height": 900})
            _attach_browser_diagnostics(
                interaction_page,
                "interaction",
                console_errors,
                page_errors,
            )
            _open_reader(interaction_page, fixture_url, expect)
            _exercise_rapid_workspace_switching(interaction_page, expect)
            _exercise_export_menu(interaction_page, expect)
            _exercise_drawers(interaction_page, expect)
            interaction_page.close()

            reduced_page = browser.new_page(viewport={"width": 1440, "height": 900})
            reduced_page.emulate_media(reduced_motion="reduce")
            _attach_browser_diagnostics(
                reduced_page,
                "reduced-motion",
                console_errors,
                page_errors,
            )
            _open_reader(reduced_page, fixture_url, expect)
            _assert_reduced_motion(reduced_page, expect)
            reduced_page.close()

            for width, height in VIEWPORTS:
                label = f"overflow-{width}x{height}"
                page = browser.new_page(viewport={"width": width, "height": height})
                _attach_browser_diagnostics(page, label, console_errors, page_errors)
                _open_reader(page, fixture_url, expect)
                _exercise_viewport_overflow(
                    page,
                    expect,
                    screenshot_root,
                    width,
                    height,
                )
                page.close()

            browser.close()
    except (AssertionError, Error) as error:
        failure = str(error)

    if failure is not None:
        print(f"Playwright motion QA failed: {failure}")
    if console_errors:
        print("Browser console errors:")
        for error in console_errors:
            print(f"- {error}")
    if page_errors:
        print("Browser page errors:")
        for error in page_errors:
            print(f"- {error}")
    if failure is not None or console_errors or page_errors:
        return 1

    print("Playwright motion QA passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
