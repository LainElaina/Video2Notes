from __future__ import annotations

import unittest

from pydantic import ValidationError

from video2notes.notes import OutputFormat, ReportPreset, ReportSpec
from video2notes.pipeline import PipelineRequest
from video2notes.sources import SourceInput


class ReportSpecTests(unittest.TestCase):
    def test_all_presets_resolve_to_distinct_audiences(self) -> None:
        resolved = {
            preset: ReportSpec(preset=preset).resolve()
            for preset in ReportPreset
        }
        self.assertEqual(len({item.audience for item in resolved.values()}), len(ReportPreset))
        self.assertLess(
            resolved[ReportPreset.CONCISE].max_sections,
            resolved[ReportPreset.DETAILED].max_sections,
        )
        self.assertFalse(resolved[ReportPreset.EXECUTIVE].include_glossary)
        self.assertTrue(resolved[ReportPreset.BEGINNER].include_glossary)

    def test_markdown_cannot_be_disabled(self) -> None:
        with self.assertRaisesRegex(ValidationError, "canonical output"):
            ReportSpec(output_formats={OutputFormat.HTML, OutputFormat.PDF})

    def test_explicit_limits_override_preset_defaults(self) -> None:
        resolved = ReportSpec(
            preset=ReportPreset.DETAILED,
            max_sections=9,
            max_takeaways=3,
            include_glossary=False,
            output_formats={OutputFormat.MARKDOWN, OutputFormat.HTML},
        ).resolve()
        self.assertEqual(resolved.max_sections, 9)
        self.assertEqual(resolved.max_takeaways, 3)
        self.assertFalse(resolved.include_glossary)
        self.assertNotIn(OutputFormat.PDF, resolved.output_formats)

    def test_pipeline_legacy_flags_resolve_without_changing_old_requests(self) -> None:
        request = PipelineRequest(
            source=SourceInput.local("C:/video.mp4"),
            include_screenshots=False,
            generate_pdf=False,
        )
        resolved = request.effective_report_spec().resolve()
        self.assertFalse(resolved.include_screenshots)
        self.assertEqual(
            resolved.output_formats,
            {OutputFormat.MARKDOWN, OutputFormat.HTML},
        )

    def test_explicit_pipeline_report_takes_precedence(self) -> None:
        request = PipelineRequest(
            source=SourceInput.local("C:/video.mp4"),
            include_screenshots=True,
            generate_pdf=True,
            report_spec=ReportSpec(
                preset=ReportPreset.EXECUTIVE,
                include_screenshots=False,
                output_formats={OutputFormat.MARKDOWN, OutputFormat.HTML},
            ),
        )
        resolved = request.effective_report_spec().resolve()
        self.assertEqual(resolved.preset, ReportPreset.EXECUTIVE)
        self.assertFalse(resolved.include_screenshots)
        self.assertNotIn(OutputFormat.PDF, resolved.output_formats)
