from __future__ import annotations

import unittest

from video2notes.ocr import (
    OcrBox,
    OcrFrameStatus,
    OcrLine,
    OcrLineDecision,
    OcrModelInvocation,
    OcrResult,
    box_iou,
    select_scroll_keyframes,
    track_ocr_lines,
)


def _invocation() -> OcrModelInvocation:
    return OcrModelInvocation(
        engine="fake",
        version="1",
        backend="fake",
        local_models_only=True,
    )


def _line(
    line_id: str,
    text: str,
    *,
    x: float,
    y: float,
    width: float = 100,
    height: float = 20,
    confidence: float = 0.95,
) -> OcrLine:
    return OcrLine(
        id=line_id,
        raw_text=text,
        normalized_text=text,
        box=OcrBox(x=x, y=y, width=width, height=height),
        script="latin",
        confidence=confidence,
        crop_readability=0.8,
        decision=OcrLineDecision.ACCEPTED,
    )


def _result(
    result_id: str,
    state_id: str,
    keyframe_us: int,
    lines: list[OcrLine],
    *,
    readability: float = 0.8,
) -> OcrResult:
    return OcrResult(
        id=result_id,
        run_id="run",
        visual_state_id=state_id,
        keyframe_artifact=None,
        keyframe_pts=keyframe_us,
        keyframe_time_base=None,
        keyframe_us=keyframe_us,
        state_start_us=max(0, keyframe_us - 10),
        state_end_us=keyframe_us + 10,
        image_width=1920,
        image_height=1080,
        frame_readability=readability,
        status=OcrFrameStatus.PROCESSED,
        lines=lines,
        invocation=_invocation(),
    )


class OcrTrackingTests(unittest.TestCase):
    def test_box_iou(self) -> None:
        left = OcrBox(x=0, y=0, width=100, height=20)
        right = OcrBox(x=50, y=0, width=100, height=20)
        self.assertAlmostEqual(box_iou(left, right), 1 / 3)

    def test_tracks_adjacent_lines_and_reports_token_changes(self) -> None:
        first = _result(
            "result-1",
            "state-1",
            100,
            [
                _line("line-1", "Step one", x=10, y=10),
                _line("line-old", "obsolete", x=10, y=80),
            ],
        )
        second = _result(
            "result-2",
            "state-2",
            250,
            [
                _line("line-2", "Step one complete", x=12, y=10),
                _line("line-new", "fresh item", x=500, y=80),
            ],
        )

        tracking = track_ocr_lines([second, first])

        self.assertEqual(len(tracking.deltas), 1)
        delta = tracking.deltas[0]
        self.assertEqual(delta.previous_state_id, "state-1")
        self.assertEqual(delta.current_state_id, "state-2")
        self.assertEqual(len(delta.matches), 1)
        self.assertEqual(delta.matches[0].previous_line_id, "line-1")
        self.assertEqual(delta.matches[0].current_line_id, "line-2")
        self.assertEqual(delta.matches[0].added_tokens, ["complete"])
        self.assertEqual(delta.matches[0].removed_tokens, [])
        self.assertEqual(delta.added_line_ids, ["line-new"])
        self.assertEqual(delta.removed_line_ids, ["line-old"])
        self.assertIn("fresh", delta.added_tokens)
        self.assertIn("obsolete", delta.removed_tokens)

        assignments = {item.line_id: item.track_id for item in tracking.assignments}
        self.assertEqual(assignments["line-1"], assignments["line-2"])
        self.assertNotEqual(assignments["line-2"], assignments["line-new"])


class ScrollFrameSelectionTests(unittest.TestCase):
    def test_greedy_cover_prefers_rare_tokens_and_explains_scores(self) -> None:
        results = [
            _result(
                "frame-a",
                "state-a",
                100,
                [_line("a", "alpha beta", x=0, y=0)],
                readability=0.8,
            ),
            _result(
                "frame-b",
                "state-b",
                200,
                [_line("b", "beta gamma", x=0, y=0)],
                readability=0.99,
            ),
            _result(
                "frame-c",
                "state-c",
                300,
                [_line("c", "gamma delta", x=0, y=0)],
                readability=0.8,
            ),
        ]

        selection = select_scroll_keyframes(results)

        self.assertEqual(selection.coverage_ratio, 1.0)
        self.assertEqual(len(selection.selected_frames), 2)
        self.assertEqual(
            {frame.result_id for frame in selection.selected_frames},
            {"frame-a", "frame-c"},
        )
        self.assertEqual(
            set(selection.all_unique_tokens),
            {"alpha", "beta", "gamma", "delta"},
        )
        for index, frame in enumerate(selection.selected_frames, start=1):
            self.assertEqual(frame.selection_order, index)
            self.assertGreater(frame.weighted_coverage_gain, 0)
            self.assertGreater(frame.rarity_bonus, 0)
            self.assertGreater(frame.selection_score, 0)
            self.assertTrue(frame.newly_covered_tokens)

    def test_empty_text_needs_no_frames(self) -> None:
        selection = select_scroll_keyframes([_result("empty", "empty-state", 1, [])])
        self.assertEqual(selection.selected_frames, [])
        self.assertEqual(selection.coverage_ratio, 1.0)


if __name__ == "__main__":
    unittest.main()
