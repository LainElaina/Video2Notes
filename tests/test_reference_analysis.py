from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from video2notes.evaluation.diagnostics import RunProfileSetError
from video2notes.evaluation.reference_analysis import (
    analyze_reference_runs,
    render_reference_analysis_markdown,
    write_reference_analysis,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _build_run(
    session: Path,
    *,
    profile: str,
    beam_size: int,
    asr_device: str,
    asr_text: tuple[str, ...],
    event_times_us: tuple[int, ...],
    ocr_texts: tuple[str, ...],
    stage_seconds: tuple[float, float, float],
) -> Path:
    run = session / "runs" / profile / f"reference-{profile}"
    stages = {
        "vision.scan": {"wall_time_seconds": stage_seconds[0]},
        "audio.asr": {"wall_time_seconds": stage_seconds[1]},
        "ocr.extract": {"wall_time_seconds": stage_seconds[2]},
    }
    _write_json(
        run / "manifest.json",
        {"profile": profile, "status": "completed", "stages": stages},
    )
    _write_json(
        run / "system" / "execution-plan.json",
        {
            "effective_plan": {
                "asr_device": asr_device,
                "asr_compute_type": "float16" if asr_device == "cuda" else "int8",
                "asr_beam_size": beam_size,
                "asr_model_class": "small",
                "ocr_device": "cpu",
                "ocr_model_class": "mobile",
                "analysis_width": 480 + beam_size * 100,
                "cheap_scan_fps": 2.0,
                "expensive_scan_fps": 6.0,
                "max_fixed_samples": 100,
                "ocr_inference_max_width": 720,
                "verification_passes": beam_size // 5,
                "screenshot_budget_per_section": beam_size // 2,
            },
            "actual_backends": {
                "asr_primary": {
                    "config": {
                        "device": asr_device,
                        "compute_type": ("float16" if asr_device == "cuda" else "int8"),
                        "beam_size": beam_size,
                    }
                },
                "ocr": {"config": {"device": "cpu"}},
            },
        },
    )
    events = [
        {
            "transition_us": timestamp,
            "keyframe_us": timestamp,
            "reason": "initial" if index == 0 else "text_or_ui_change",
        }
        for index, timestamp in enumerate(event_times_us)
    ]
    _write_json(
        run / "vision" / "scan-events.json",
        {
            "scanner": {
                "analysis_width": 480 + beam_size * 100,
                "analysis_height": 270,
                "coarse_fps": 2.0,
                "fine_fps": 6.0,
            },
            "events": events,
        },
    )
    _write_json(
        run / "vision" / "visual-states.json",
        [{"id": f"state-{index}"} for index, _ in enumerate(event_times_us)],
    )
    _write_json(
        run / "asr" / "asr-evidence.json",
        [
            {
                "id": f"asr-{index}",
                "start_us": index * 1_000_000,
                "end_us": (index + 1) * 1_000_000,
                "normalized_text": text,
            }
            for index, text in enumerate(asr_text)
        ],
    )
    ocr_results = []
    ocr_evidence = []
    for index, text in enumerate(ocr_texts):
        start_us = index * 1_000_000 + (500_000 if profile != "fast" else 0)
        end_us = start_us + 750_000
        ocr_results.append(
            {
                "status": "processed",
                "lines": [
                    {
                        "decision": "accepted",
                        "normalized_text": text,
                    },
                    {
                        "decision": "abstained",
                        "normalized_text": "noise",
                    },
                ],
            }
        )
        ocr_evidence.append(
            {
                "id": f"ocr-{index}",
                "start_us": start_us,
                "end_us": end_us,
                "normalized_text": text,
            }
        )
    _write_json(
        run / "ocr" / "ocr-evidence.json",
        {"bundle": {"results": ocr_results, "evidence": ocr_evidence}},
    )
    _write_json(
        run / "notes" / "document.json",
        {
            "sections": [
                {
                    "screenshots": (
                        []
                        if profile == "fast"
                        else [{"relative_path": f"notes/assets/{profile}.jpg"}]
                    )
                }
            ],
            "facts": [{"id": f"fact-{index}"} for index in range(len(ocr_texts))],
        },
    )
    _write_json(
        session / "reports" / f"{profile}.resource.json",
        {
            "duration_seconds": sum(stage_seconds) + 0.5,
            "baseline": {
                "sample_count": 2,
                "average_nvidia_gpu_percent": 10.0,
                "peak_nvidia_gpu_percent": 12.0,
                "average_nvidia_vram_used_bytes": 1024**3,
                "peak_nvidia_vram_used_bytes": 2 * 1024**3,
            },
            "run": {
                "sample_count": 4,
                "average_process_tree_cpu_percent": 20.0,
                "peak_process_tree_cpu_percent": 30.0,
                "average_process_tree_rss_bytes": 512 * 1024**2,
                "peak_process_tree_rss_bytes": 768 * 1024**2,
                "average_nvidia_gpu_percent": 40.0,
                "peak_nvidia_gpu_percent": 60.0,
                "average_nvidia_vram_used_bytes": 3 * 1024**3,
                "peak_nvidia_vram_used_bytes": 4 * 1024**3,
            },
        },
    )
    return run


class ReferenceAnalysisTests(unittest.TestCase):
    def test_analysis_separates_cost_consistency_and_accuracy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session = Path(temporary)
            _write_json(session / "benchmark-manifest.json", {"schema_version": 1})
            fast = _build_run(
                session,
                profile="fast",
                beam_size=1,
                asr_device="cuda",
                asr_text=("你好 世界",),
                event_times_us=(0, 2_000_000),
                ocr_texts=("设置推流",),
                stage_seconds=(1.0, 2.0, 3.0),
            )
            balanced = _build_run(
                session,
                profile="balanced",
                beam_size=5,
                asr_device="cuda",
                asr_text=("你好，世界", "继续"),
                event_times_us=(400_000, 2_800_000, 4_000_000),
                ocr_texts=("设置推流", "直播地址"),
                stage_seconds=(2.0, 2.5, 5.0),
            )
            accurate = _build_run(
                session,
                profile="accurate",
                beam_size=5,
                asr_device="cuda",
                asr_text=("你好，世界", "继续讲解"),
                event_times_us=(450_000, 2_850_000, 4_100_000, 5_000_000),
                ocr_texts=("设置推流", "直播地址", "串流密钥"),
                stage_seconds=(3.0, 2.5, 8.0),
            )

            result = analyze_reference_runs(session, (accurate, fast, balanced))

            self.assertFalse(result.accuracy_claim_supported)
            self.assertEqual(
                [item.profile for item in result.profiles],
                ["fast", "balanced", "accurate"],
            )
            self.assertEqual(result.profiles[0].engine.asr_device, "cuda")
            self.assertEqual(result.profiles[0].engine.asr_beam_size, 1)
            self.assertEqual(result.profiles[1].engine.asr_beam_size, 5)
            self.assertEqual(result.profiles[1].ocr_raw_accepted_line_count, 2)
            self.assertEqual(result.profiles[1].ocr_merged_evidence_count, 2)
            self.assertEqual(result.profiles[1].ocr_unique_normalized_text_count, 2)
            self.assertEqual(result.profiles[2].visual_state_count, 4)
            self.assertEqual(result.profiles[2].screenshot_count, 1)
            self.assertEqual(result.profiles[2].fact_count, 3)
            self.assertEqual(result.profiles[2].section_count, 1)
            self.assertEqual(
                result.profiles[0].resource.measurement_scope,
                "nvidia_device_wide_not_process_attributed",
            )

            fast_to_balanced = result.transitions[0]
            self.assertEqual(
                fast_to_balanced.evaluation_kind,
                "no_ground_truth_cross_tier_consistency",
            )
            self.assertFalse(fast_to_balanced.accuracy_claim_supported)
            self.assertEqual(
                fast_to_balanced.visual_event_retained_within_500ms_ratio,
                0.0,
            )
            self.assertEqual(
                fast_to_balanced.visual_event_retained_within_1s_ratio,
                1.0,
            )
            self.assertEqual(
                fast_to_balanced.ocr_time_and_text_consistency_ratio,
                1.0,
            )

            markdown = render_reference_analysis_markdown(result)
            self.assertIn("不能把证据数量", markdown)
            self.assertIn("不是 OCR 准确率", markdown)
            self.assertIn("整卡 GPU / 显存遥测", markdown)
            self.assertIn("各档实际增加了什么", markdown)

            json_path, markdown_path = write_reference_analysis(
                session,
                (fast, balanced, accurate),
            )
            self.assertEqual(json_path.name, "detailed-comparison.json")
            self.assertEqual(markdown_path.name, "detailed-comparison.md")
            saved = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertFalse(saved["accuracy_claim_supported"])
            self.assertEqual(len(saved["transitions"]), 2)

    def test_analysis_requires_all_three_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            session = Path(temporary)
            _write_json(session / "benchmark-manifest.json", {"schema_version": 1})
            fast = _build_run(
                session,
                profile="fast",
                beam_size=1,
                asr_device="cpu",
                asr_text=("text",),
                event_times_us=(0,),
                ocr_texts=("screen",),
                stage_seconds=(1.0, 1.0, 1.0),
            )

            with self.assertRaisesRegex(RunProfileSetError, "requires fast"):
                analyze_reference_runs(session, (fast,))


if __name__ == "__main__":
    unittest.main()
