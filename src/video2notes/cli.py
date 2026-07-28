"""Command-line entry point for the current Video2Notes research kernel."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from .vision.adaptive_sampler import AdaptiveScanConfig, AdaptiveVideoScanner


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video2notes",
        description="High-precision, evidence-first video note tooling.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser(
        "scan-changes",
        help="discover persistent visual changes with adaptive two-pass scanning",
    )
    scan.add_argument("video", type=Path, help="local video file")
    scan.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/visual-events.json"),
        help="JSON event manifest",
    )
    scan.add_argument(
        "--preview-dir",
        type=Path,
        default=None,
        help="optionally extract original-resolution event previews",
    )
    scan.add_argument("--coarse-fps", type=float, default=3.0)
    scan.add_argument("--fine-fps", type=float, default=12.0)
    scan.add_argument("--analysis-width", type=int, default=640)
    scan.add_argument("--analysis-height", type=int, default=360)
    scan.add_argument(
        "--print-config",
        action="store_true",
        help="print the resolved detector configuration before scanning",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "scan-changes":
        config = AdaptiveScanConfig(
            coarse_fps=args.coarse_fps,
            fine_fps=args.fine_fps,
            analysis_width=args.analysis_width,
            analysis_height=args.analysis_height,
        )
        if args.print_config:
            print(json.dumps(asdict(config), ensure_ascii=False, indent=2))
        scanner = AdaptiveVideoScanner(config)
        result = scanner.scan(args.video, preview_dir=args.preview_dir)
        output = result.write_json(args.output)
        print(
            f"Detected {len(result.events)} stable visual states; "
            f"manifest: {output.resolve()}"
        )
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2

