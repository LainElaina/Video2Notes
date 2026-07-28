"""Deterministic Markdown/HTML renderers and system-Chromium PDF export."""

from __future__ import annotations

import base64
import html
import mimetypes
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from urllib.parse import quote

from markdown_it import MarkdownIt

from .models import NoteDocument, NoteScreenshot, NoteSection

ProcessRunner = Callable[
    [Sequence[str]],
    subprocess.CompletedProcess[str],
]


def format_timestamp(time_us: int) -> str:
    total_seconds = max(0, time_us) // 1_000_000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _seek_uri(note: NoteDocument, time_us: int) -> str:
    run_id = quote(note.metadata.run_id, safe="")
    return f"video2notes://seek/{run_id}?time_us={time_us}"


def _yaml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def render_markdown(note: NoteDocument) -> str:
    """Render the primary portable output with evidence IDs kept in comments."""

    metadata = note.metadata
    lines = [
        "---",
        f"title: {_yaml_string(metadata.title)}",
        f"run_id: {_yaml_string(metadata.run_id)}",
        f"source_kind: {_yaml_string(metadata.source_kind)}",
        f"source: {_yaml_string(metadata.source_url or metadata.source_locator)}",
        f"duration_us: {metadata.duration_us}",
        f"quality_mode: {_yaml_string(metadata.quality_mode)}",
        f"created_at: {_yaml_string(metadata.created_at.isoformat())}",
        "---",
        "",
        f"# {metadata.title}",
        "",
    ]
    for warning in metadata.quality_warnings:
        lines.extend([f"> **画质/识别提示：** {warning}", ""])

    lines.extend(
        [
            "## 概览",
            "",
            note.abstract.strip(),
            "",
        ]
    )
    if note.key_takeaways:
        lines.extend(["## 核心要点", ""])
        lines.extend(f"- {item}" for item in note.key_takeaways)
        lines.append("")

    lines.extend(["## 目录", ""])
    for section in note.sections:
        timestamp = format_timestamp(section.start_us)
        lines.append(
            f"- [{timestamp}]({_seek_uri(note, section.start_us)}) [{section.title}](#{section.id})"
        )
    lines.append("")

    for section in note.sections:
        lines.extend(_render_markdown_section(note, section))

    if note.glossary:
        lines.extend(["## 术语表", "", "| 术语 | 说明 |", "|---|---|"])
        for term, definition in note.glossary.items():
            safe_term = term.replace("|", r"\|")
            safe_definition = definition.replace("|", r"\|")
            lines.append(f"| {safe_term} | {safe_definition} |")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_markdown_section(note: NoteDocument, section: NoteSection) -> list[str]:
    start = format_timestamp(section.start_us)
    end = format_timestamp(section.end_us)
    lines = [
        f'<a id="{section.id}"></a>',
        "",
        f"## {section.title}",
        "",
        f"**时间：** [{start}]({_seek_uri(note, section.start_us)})–{end}",
        "",
    ]
    if section.summary.strip():
        lines.extend([f"> {section.summary.strip()}", ""])
    if section.body_markdown.strip():
        lines.extend([section.body_markdown.strip(), ""])
    for screenshot in section.screenshots:
        lines.extend(
            [
                f"![{screenshot.alt_text}]({screenshot.relative_path.replace(' ', '%20')})",
                "",
                f"*[{format_timestamp(screenshot.timestamp_us)}]"
                f"({_seek_uri(note, screenshot.timestamp_us)}) · {screenshot.caption}*",
                "",
            ]
        )
    if section.evidence_ids:
        lines.extend(
            [
                f"<!-- evidence: {', '.join(section.evidence_ids)} -->",
                "",
            ]
        )
    return lines


def write_markdown(note: NoteDocument, destination: str | Path) -> Path:
    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(target, render_markdown(note))
    return target


def render_html(
    note: NoteDocument,
    *,
    artifact_root: str | Path | None = None,
) -> str:
    """Render a self-contained reading view; local screenshots are data URLs."""

    md = MarkdownIt("commonmark", {"html": False, "linkify": False})
    sections = "\n".join(
        _render_html_section(note, section, md, artifact_root=artifact_root)
        for section in note.sections
    )
    toc = "\n".join(
        (
            f'<a class="toc-row" href="#{html.escape(section.id)}">'
            f"<time>{format_timestamp(section.start_us)}</time>"
            f"<span>{html.escape(section.title)}</span></a>"
        )
        for section in note.sections
    )
    takeaways = ""
    if note.key_takeaways:
        takeaways = (
            '<section class="takeaways"><p class="eyebrow">核心要点</p><ul>'
            + "".join(f"<li>{html.escape(item)}</li>" for item in note.key_takeaways)
            + "</ul></section>"
        )
    warnings = "".join(
        f'<div class="quality-warning"><span>画质提示</span>{html.escape(item)}</div>'
        for item in note.metadata.quality_warnings
    )
    languages = " · ".join(note.metadata.languages) or "未标注"
    source = note.metadata.source_url or note.metadata.source_locator
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>{html.escape(note.metadata.title)}</title>
  <style>{_NOTE_CSS}</style>
</head>
<body>
  <header class="masthead">
    <div class="masthead-rule"></div>
    <p class="product">VIDEO2NOTES / EVIDENCE EDITION</p>
    <h1>{html.escape(note.metadata.title)}</h1>
    <p class="abstract">{html.escape(note.abstract)}</p>
    <div class="metadata">
      <span>{html.escape(note.metadata.source_kind.upper())}</span>
      <span>{format_timestamp(note.metadata.duration_us)}</span>
      <span>{html.escape(languages)}</span>
      <span>{html.escape(note.metadata.quality_mode.upper())}</span>
    </div>
    <p class="source">{html.escape(source)}</p>
  </header>
  <main>
    {warnings}
    <div class="opening-grid">
      <nav class="toc" aria-label="目录">
        <p class="eyebrow">时间目录</p>
        {toc}
      </nav>
      {takeaways}
    </div>
    <div class="section-stack">{sections}</div>
  </main>
  <footer>
    <span>Video2Notes · 证据优先笔记</span>
    <span>Run {html.escape(note.metadata.run_id)}</span>
  </footer>
</body>
</html>
"""


def _render_html_section(
    note: NoteDocument,
    section: NoteSection,
    markdown: MarkdownIt,
    *,
    artifact_root: str | Path | None,
) -> str:
    screenshots = "".join(
        _render_html_screenshot(
            note,
            screenshot,
            artifact_root=artifact_root,
        )
        for screenshot in section.screenshots
    )
    evidence = " ".join(
        f"<code>{html.escape(evidence_id)}</code>" for evidence_id in section.evidence_ids
    )
    evidence_block = (
        f"<details><summary>证据索引 · {len(section.evidence_ids)}</summary>"
        f'<div class="evidence-list">{evidence}</div></details>'
        if section.evidence_ids
        else ""
    )
    start_uri = html.escape(_seek_uri(note, section.start_us), quote=True)
    return f"""
<article id="{html.escape(section.id)}" class="note-section">
  <aside class="evidence-rail" aria-hidden="true">
    <span class="rail-dot"></span><span class="rail-line"></span>
  </aside>
  <div class="section-content">
    <a class="timecode" href="{start_uri}">{format_timestamp(section.start_us)}</a>
    <h2>{html.escape(section.title)}</h2>
    <p class="section-summary">{html.escape(section.summary)}</p>
    <div class="prose">{markdown.render(section.body_markdown)}</div>
    {screenshots}
    {evidence_block}
  </div>
</article>
"""


def _render_html_screenshot(
    note: NoteDocument,
    screenshot: NoteScreenshot,
    *,
    artifact_root: str | Path | None,
) -> str:
    source = _image_source(screenshot.relative_path, artifact_root)
    timestamp_uri = html.escape(
        _seek_uri(note, screenshot.timestamp_us),
        quote=True,
    )
    return f"""
<figure>
  <img src="{html.escape(source, quote=True)}" alt="{html.escape(screenshot.alt_text)}">
  <figcaption>
    <a href="{timestamp_uri}">{format_timestamp(screenshot.timestamp_us)}</a>
    <span>{html.escape(screenshot.caption)}</span>
  </figcaption>
</figure>
"""


def _image_source(relative_path: str, artifact_root: str | Path | None) -> str:
    if artifact_root is None:
        return quote(relative_path)
    root = Path(artifact_root).expanduser().resolve()
    image_path = (root / relative_path).resolve()
    if not image_path.is_relative_to(root) or not image_path.is_file():
        return quote(relative_path)
    media_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def write_html(
    note: NoteDocument,
    destination: str | Path,
    *,
    artifact_root: str | Path | None = None,
) -> Path:
    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(target, render_html(note, artifact_root=artifact_root))
    return target


def render_pdf_from_html(
    html_path: str | Path,
    destination: str | Path,
    *,
    browser_executable: str | Path | None = None,
    runner: ProcessRunner | None = None,
) -> Path:
    """Print HTML with an installed Chromium browser without re-generating content."""

    source = Path(html_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"HTML note does not exist: {source}")
    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    browser = (
        Path(browser_executable).expanduser().resolve()
        if browser_executable is not None
        else _find_chromium()
    )
    if not browser.is_file():
        raise FileNotFoundError(f"Chromium browser does not exist: {browser}")

    process_runner = runner or _default_process_runner
    with tempfile.TemporaryDirectory(prefix="video2notes-pdf-") as profile_dir:
        command = (
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--user-data-dir={profile_dir}",
            f"--print-to-pdf={target}",
            source.as_uri(),
        )
        result = process_runner(command)
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"PDF rendering failed: {message[-1000:]}")
    if not target.is_file() or target.stat().st_size == 0:
        raise RuntimeError("PDF renderer exited successfully but produced no file")
    return target


def _default_process_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def _find_chromium() -> Path:
    command = shutil.which("msedge") or shutil.which("chrome") or shutil.which("chromium")
    if command:
        return Path(command).resolve()

    roots = [
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("PROGRAMFILES"),
        os.environ.get("LOCALAPPDATA"),
    ]
    relative_candidates = (
        Path("Microsoft/Edge/Application/msedge.exe"),
        Path("Google/Chrome/Application/chrome.exe"),
        Path("Chromium/Application/chrome.exe"),
    )
    for root in roots:
        if not root:
            continue
        for relative in relative_candidates:
            candidate = (Path(root) / relative).resolve()
            if candidate.is_file():
                return candidate
    raise FileNotFoundError("No installed Edge/Chrome/Chromium browser was found for PDF export")


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


_NOTE_CSS = r"""
@page {
  size: A4;
  margin: 18mm 16mm 20mm;
}
:root {
  --desk: #eef1ef;
  --paper: #fcfdfb;
  --ink: #172125;
  --muted: #63716f;
  --line: #ccd5d2;
  --teal: #176c70;
  --vermilion: #e65d3e;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0;
  color: var(--ink);
  background: var(--desk);
  font: 16px/1.75 "Noto Sans SC", "Microsoft YaHei UI", "Segoe UI", sans-serif;
}
.masthead, main, footer {
  width: min(1020px, calc(100% - 40px));
  margin-inline: auto;
}
.masthead {
  padding: 64px 0 40px;
}
.masthead-rule {
  width: 72px;
  height: 6px;
  margin-bottom: 32px;
  background: var(--vermilion);
}
.product, .eyebrow {
  margin: 0 0 12px;
  color: var(--teal);
  font: 700 12px/1.2 "IBM Plex Sans Condensed", "Arial Narrow", sans-serif;
  letter-spacing: .16em;
  text-transform: uppercase;
}
h1 {
  max-width: 900px;
  margin: 0;
  font: 650 clamp(42px, 7vw, 82px)/.98 "IBM Plex Sans Condensed",
    "Noto Sans SC", "Microsoft YaHei UI", sans-serif;
  letter-spacing: -.045em;
}
.abstract {
  max-width: 760px;
  margin: 28px 0 24px;
  color: #334143;
  font-size: 19px;
}
.metadata {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  color: var(--muted);
  font: 700 12px/1.4 "Cascadia Mono", monospace;
  letter-spacing: .06em;
}
.source {
  max-width: 100%;
  margin: 12px 0 0;
  overflow-wrap: anywhere;
  color: var(--muted);
  font-size: 12px;
}
main {
  padding-bottom: 80px;
}
.quality-warning {
  display: grid;
  grid-template-columns: 110px 1fr;
  margin-bottom: 12px;
  padding: 14px 16px;
  border-left: 4px solid var(--vermilion);
  background: #fff7f2;
}
.quality-warning span {
  color: var(--vermilion);
  font-weight: 750;
}
.opening-grid {
  display: grid;
  grid-template-columns: minmax(260px, .8fr) minmax(320px, 1.2fr);
  gap: 1px;
  margin: 42px 0 72px;
  border: 1px solid var(--line);
  background: var(--line);
}
.toc, .takeaways {
  padding: 28px;
  background: var(--paper);
}
.toc-row {
  display: grid;
  grid-template-columns: 70px 1fr;
  gap: 12px;
  padding: 9px 0;
  color: inherit;
  text-decoration: none;
  border-bottom: 1px solid #e5eae8;
}
.toc-row:hover { color: var(--teal); }
time, .timecode, figure a {
  color: var(--vermilion);
  font: 700 12px/1.6 "Cascadia Mono", monospace;
  text-decoration: none;
}
.takeaways ul {
  margin: 0;
  padding-left: 20px;
}
.takeaways li { margin: 0 0 12px; }
.section-stack {
  background: var(--paper);
  border: 1px solid var(--line);
}
.note-section {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
  break-inside: avoid-page;
}
.note-section + .note-section .section-content {
  border-top: 1px solid var(--line);
}
.evidence-rail {
  position: relative;
  display: flex;
  justify-content: center;
}
.rail-dot {
  position: relative;
  z-index: 1;
  width: 11px;
  height: 11px;
  margin-top: 43px;
  border: 3px solid var(--paper);
  border-radius: 50%;
  background: var(--vermilion);
  box-shadow: 0 0 0 1px var(--vermilion);
}
.rail-line {
  position: absolute;
  inset: 0 auto;
  width: 1px;
  background: var(--line);
}
.section-content {
  min-width: 0;
  padding: 38px 46px 48px 20px;
}
.section-content h2 {
  margin: 4px 0 14px;
  font: 650 34px/1.15 "IBM Plex Sans Condensed",
    "Noto Sans SC", "Microsoft YaHei UI", sans-serif;
  letter-spacing: -.025em;
}
.section-summary {
  margin: 0 0 26px;
  color: var(--teal);
  font-size: 17px;
  font-weight: 650;
}
.prose h3 { margin-top: 32px; font-size: 20px; }
.prose pre {
  overflow-x: auto;
  padding: 18px;
  color: #eaf0ee;
  background: #202a2c;
}
.prose code, .evidence-list code {
  font-family: "Cascadia Mono", "JetBrains Mono", monospace;
}
.prose :not(pre) > code {
  padding: .12em .35em;
  background: #e8efec;
}
.prose blockquote {
  margin: 24px 0;
  padding: 4px 0 4px 20px;
  border-left: 2px solid var(--teal);
  color: #435152;
}
.prose table {
  width: 100%;
  border-collapse: collapse;
}
.prose th, .prose td {
  padding: 8px 10px;
  border: 1px solid var(--line);
  text-align: left;
}
figure {
  margin: 34px 0 20px;
}
figure img {
  display: block;
  width: 100%;
  max-height: 640px;
  object-fit: contain;
  border: 1px solid var(--line);
  background: #151c1e;
}
figcaption {
  display: grid;
  grid-template-columns: 70px 1fr;
  gap: 12px;
  padding: 10px 0;
  color: var(--muted);
  font-size: 13px;
}
details {
  margin-top: 30px;
  color: var(--muted);
  font-size: 12px;
}
summary { cursor: pointer; }
.evidence-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}
.evidence-list code {
  padding: 3px 6px;
  border: 1px solid var(--line);
}
footer {
  display: flex;
  justify-content: space-between;
  padding: 22px 0 36px;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font: 11px/1.4 "Cascadia Mono", monospace;
}
@media (max-width: 700px) {
  .masthead, main, footer { width: min(100% - 24px, 1020px); }
  .masthead { padding-top: 36px; }
  .opening-grid { grid-template-columns: 1fr; }
  .note-section { grid-template-columns: 30px minmax(0, 1fr); }
  .section-content { padding: 28px 18px 36px 4px; }
  .quality-warning { grid-template-columns: 1fr; }
}
@media print {
  body { background: #fff; font-size: 10.5pt; }
  .masthead, main, footer { width: 100%; }
  .masthead { padding-top: 0; }
  .opening-grid { margin: 24px 0 36px; }
  .section-stack { border-color: #bfc7c4; }
  a { color: inherit; }
  details { display: none; }
}
"""
