from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from video2notes.artifacts import RunWorkspace
from video2notes.domain import ArtifactKind, SourceDescriptor, StageStatus


class RunWorkspaceTests(unittest.TestCase):
    def create_workspace(self, root: Path, run_id: str = "run-a") -> RunWorkspace:
        return RunWorkspace.create(
            root,
            run_id=run_id,
            source=SourceDescriptor(kind="local", locator="sample.mp4"),
            profile="fast",
        )

    def test_completed_stage_is_reused_only_when_output_hash_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.create_workspace(Path(temporary))
            output = workspace.artifact_path("media", "probe.json")
            with workspace.stage(
                "media.probe",
                stage_version="1",
                config={"detail": "full"},
            ) as stage:
                self.assertFalse(stage.cached)
                output.write_text('{"ok": true}', encoding="utf-8")
                stage.add_output(output, kind=ArtifactKind.MEDIA)

            reloaded = RunWorkspace(workspace.root)
            with reloaded.stage(
                "media.probe",
                stage_version="1",
                config={"detail": "full"},
            ) as cached:
                self.assertTrue(cached.cached)
                self.assertEqual(len(cached.outputs), 1)

            output.write_text('{"ok": false}', encoding="utf-8")
            changed = RunWorkspace(workspace.root)
            with changed.stage(
                "media.probe",
                stage_version="1",
                config={"detail": "full"},
            ) as rerun:
                self.assertFalse(rerun.cached)
                self.assertEqual(rerun.record.attempt if rerun.record else 0, 2)
                output.write_text('{"ok": true}', encoding="utf-8")
                rerun.add_output(output, kind=ArtifactKind.MEDIA)

    def test_failure_is_persisted_and_can_be_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.create_workspace(Path(temporary))
            with (
                self.assertRaisesRegex(RuntimeError, "intentional"),
                workspace.stage(
                    "vision.scan",
                    stage_version="1",
                ),
            ):
                raise RuntimeError("intentional")

            failed = RunWorkspace(workspace.root)
            record = failed.manifest.stages["vision.scan"]
            self.assertEqual(record.status, StageStatus.FAILED)
            self.assertIn("intentional", record.error or "")

            with failed.stage("vision.scan", stage_version="1") as retry:
                self.assertFalse(retry.cached)
                self.assertEqual(retry.record.attempt if retry.record else 0, 2)

    def test_run_directories_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.create_workspace(root, "first")
            second = self.create_workspace(root, "second")
            first_file = first.artifact_path("media", "source.mp4")
            first_file.write_bytes(b"first")
            self.assertFalse(second.artifact_path("media", "source.mp4").exists())
