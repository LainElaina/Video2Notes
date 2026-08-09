from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video2notes.artifacts import RunWorkspace
from video2notes.domain import (
    ArtifactKind,
    ProcessingScope,
    RunStatus,
    SourceDescriptor,
    StageStatus,
)


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

    def test_cancelled_stage_and_warning_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.create_workspace(Path(temporary))
            with (
                self.assertRaisesRegex(RuntimeError, "stop"),
                workspace.stage(
                    "audio.asr",
                    stage_version="1",
                ),
            ):
                raise RuntimeError("stop")
            workspace.add_warning("ASR was cancelled")
            workspace.add_warning("ASR was cancelled")
            workspace.mark_cancelled(stage_name="audio.asr")

            loaded = RunWorkspace(workspace.root)
            self.assertEqual(loaded.manifest.status, RunStatus.CANCELLED)
            self.assertEqual(
                loaded.manifest.stages["audio.asr"].status,
                StageStatus.CANCELLED,
            )
            self.assertEqual(loaded.manifest.warnings, ["ASR was cancelled"])

    def test_run_directories_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.create_workspace(root, "first")
            second = self.create_workspace(root, "second")
            first_file = first.artifact_path("media", "source.mp4")
            first_file.write_bytes(b"first")
            self.assertFalse(second.artifact_path("media", "source.mp4").exists())

    def test_manifest_replace_retries_transient_windows_reader_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.create_workspace(Path(temporary))
            real_replace = os.replace
            attempts = 0

            def flaky_replace(source: Path, destination: Path) -> None:
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise PermissionError("manifest is briefly open by a reader")
                real_replace(source, destination)

            with patch("video2notes.artifacts.store.os.replace", side_effect=flaky_replace):
                workspace.set_status(RunStatus.RUNNING)

            self.assertEqual(attempts, 3)
            self.assertEqual(RunWorkspace(workspace.root).manifest.status, RunStatus.RUNNING)

    def test_manifest_replace_failure_removes_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.create_workspace(Path(temporary))

            with (
                patch(
                    "video2notes.artifacts.store.os.replace",
                    side_effect=PermissionError("manifest remains locked"),
                ),
                patch("video2notes.artifacts.store.time.sleep"),
                self.assertRaises(PermissionError),
            ):
                workspace.set_status(RunStatus.RUNNING)

            temporary_files = list(workspace.root.glob(".manifest.json.*.tmp"))
            self.assertEqual(temporary_files, [])

    def test_legacy_v1_manifest_without_scope_loads_as_audio_visual(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.create_workspace(Path(temporary))
            manifest_path = workspace.root / "manifest.json"
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["schema_version"] = 1
            payload.pop("processing_scope")
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")

            loaded = RunWorkspace(workspace.root)

            self.assertEqual(loaded.manifest.schema_version, 2)
            self.assertEqual(
                loaded.manifest.processing_scope,
                ProcessingScope.AUDIO_VISUAL,
            )
            migrated = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(migrated["schema_version"], 2)
            self.assertEqual(migrated["processing_scope"], "audio_visual")
