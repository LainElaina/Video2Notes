from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from video2notes.api import ApiContext, create_app
from video2notes.artifacts import RunWorkspace, sha256_file
from video2notes.audio import (
    ASREvidenceResult,
    ASRSegment,
    ASRTranscript,
    AudioExtractionResult,
    TranscriptTimeline,
)
from video2notes.domain import (
    ArtifactKind,
    EvidenceModality,
    EvidenceSpan,
    MediaManifest,
    MediaStream,
    MediaTimestamp,
    Rational,
    RunStatus,
    SourceDescriptor,
)
from video2notes.operations import (
    EvidenceRevision,
    OperationConflictError,
    OperationKind,
    OperationRequest,
    OperationService,
    OperationStatus,
)
from video2notes.providers import KeyringSecretStore, ModelRegistry
from video2notes.vision import (
    AdaptiveScanConfig,
    ChangeEvent,
    SamplingMode,
    SamplingSpec,
    TimeRange,
)
from video2notes.vision.adaptive_sampler import ScanResult, VideoProbe


class InMemoryKeyring:
    def set_password(self, service_name: str, username: str, password: str) -> None:
        del service_name, username, password

    def get_password(self, service_name: str, username: str) -> str | None:
        del service_name, username
        return None

    def delete_password(self, service_name: str, username: str) -> None:
        del service_name, username


class PlaceholderAsrBackend:
    def transcribe(
        self,
        audio_path: Path,
        *,
        language: str | None = None,
    ) -> ASRTranscript:
        del audio_path, language
        raise AssertionError("the injected operation transcriber should be used")


class RecordingScanner:
    def __init__(self, config: AdaptiveScanConfig) -> None:
        self.config = config
        self.calls: list[tuple[int, int]] = []

    def scan_range(
        self,
        source: str | Path,
        *,
        start_us: int,
        end_us: int,
        preview_dir: str | Path | None = None,
        probe: VideoProbe | None = None,
    ) -> ScanResult:
        del source, probe
        self.calls.append((start_us, end_us))
        if preview_dir is None:
            raise AssertionError("operation scanner must receive a preview directory")
        preview = Path(preview_dir) / "selected.jpg"
        preview.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (12, 8), (240, 240, 240)).save(preview, format="JPEG")
        time_base = Rational(numerator=1, denominator=1_000_000)
        timestamp = MediaTimestamp.from_pts(
            start_us,
            time_base,
            timeline_origin_us=0,
        )
        event = ChangeEvent(
            transition=timestamp,
            keyframe=timestamp,
            previous_keyframe=None,
            reason="initial",
            state_score=0.0,
            scene_score=0.0,
            text_score=0.0,
            step_score=0.0,
            refined=False,
            preview_path=str(preview),
        )
        return ScanResult(
            source="fixture.mp4",
            probe=VideoProbe(
                duration_us=10_000_000,
                width=12,
                height=8,
                frame_rate=30.0,
                timeline_origin_us=0,
                stream_index=0,
                stream_time_base=time_base,
            ),
            config=self.config,
            events=(event,),
        )


class OperationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runs_root = Path(self.temporary.name) / "runs"

    def test_asr_rework_uses_only_window_and_preserves_outside_evidence(self) -> None:
        outside = _evidence("asr-outside", 500_000, 1_000_000, "outside")
        inside = _evidence("asr-inside", 3_000_000, 4_000_000, "old")
        caption = _evidence(
            "caption-inside",
            3_000_000,
            4_000_000,
            "caption",
            modality=EvidenceModality.PLATFORM_CAPTION,
        )
        workspace = _completed_workspace(
            self.runs_root,
            evidence=[outside, inside, caption],
        )
        observed_windows: list[tuple[int, int]] = []

        def fake_extract(
            extraction: AudioExtractionResult,
            output_path: str | Path,
            *,
            start_us: int,
            end_us: int,
            ffmpeg_path: str = "ffmpeg",
        ) -> AudioExtractionResult:
            del ffmpeg_path
            observed_windows.append((start_us, end_us))
            destination = Path(output_path)
            destination.write_bytes(b"window")
            return AudioExtractionResult(
                input_path=extraction.output_path,
                output_path=str(destination),
                audio_stream_index=0,
                source_stream_start_us=start_us,
                timeline_origin_us=0,
                output_time_zero_canonical_us=start_us,
                duration_us=end_us - start_us,
            )

        def fake_transcribe(
            extraction: AudioExtractionResult,
            backend: PlaceholderAsrBackend,
            *,
            run_id: str,
            language: str | None = None,
            language_hints: Sequence[str] = (),
        ) -> ASREvidenceResult:
            del backend, language, language_hints
            start_us = extraction.output_time_zero_canonical_us
            end_us = start_us + extraction.duration_us
            segment = ASRSegment(
                id="new-segment",
                start_us=start_us,
                end_us=end_us,
                text="new transcript",
                provider="fake",
                model="fake-asr",
                version="1",
            )
            transcript = ASRTranscript(
                provider="fake",
                model="fake-asr",
                version="1",
                timeline=TranscriptTimeline.CANONICAL_MEDIA,
                timeline_offset_us=start_us,
                segments=[segment],
            )
            return ASREvidenceResult(
                run_id=run_id,
                transcript=transcript,
                evidence=[
                    EvidenceSpan(
                        id="generated",
                        run_id=run_id,
                        modality=EvidenceModality.ASR,
                        start_us=start_us,
                        end_us=end_us,
                        raw_text="new transcript",
                        normalized_text="new transcript",
                        provider="fake",
                        model="fake-asr",
                        version="1",
                    )
                ],
            )

        service = OperationService(
            workspace,
            asr_backend=PlaceholderAsrBackend(),
            audio_window_extractor=fake_extract,
            asr_transcriber=fake_transcribe,
        )
        request = OperationRequest(
            kind=OperationKind.ASR_RETRANSCRIBE,
            range=TimeRange(start_us=2_000_000, end_us=5_000_000),
            language_hints=["zh"],
        )
        record = service.execute(request)

        self.assertEqual(record.status, OperationStatus.COMPLETED)
        self.assertEqual(observed_windows, [(2_000_000, 5_000_000)])
        view = service.get_evidence()
        ids = {item.id for item in view.evidence}
        self.assertIn("asr-outside", ids)
        self.assertIn("caption-inside", ids)
        self.assertNotIn("asr-inside", ids)
        self.assertIn("asr-inside", view.superseded_evidence_ids)
        self.assertTrue(any(item.raw_text == "new transcript" for item in view.evidence))

    def test_vision_rescan_passes_exact_selected_range(self) -> None:
        workspace = _completed_workspace(
            self.runs_root,
            evidence=[_evidence("outside", 0, 1_000_000, "outside")],
        )
        scanner: RecordingScanner | None = None

        def scanner_factory(config: AdaptiveScanConfig) -> RecordingScanner:
            nonlocal scanner
            scanner = RecordingScanner(config)
            return scanner

        service = OperationService(
            workspace,
            scanner_factory=scanner_factory,
        )
        record = service.execute(
            OperationRequest(
                kind=OperationKind.VISION_RESCAN,
                range=TimeRange(start_us=4_000_000, end_us=6_000_000),
                sampling=SamplingSpec(mode=SamplingMode.ADAPTIVE),
                run_ocr=False,
            )
        )

        self.assertEqual(record.status, OperationStatus.COMPLETED)
        self.assertIsNotNone(scanner)
        self.assertEqual(scanner.calls if scanner is not None else [], [(4_000_000, 6_000_000)])
        self.assertEqual(record.visual_state_count, 1)
        self.assertEqual(
            {item.id for item in service.get_evidence().evidence},
            {"outside"},
        )

    def test_manual_correction_is_append_only_and_effective_view_uses_new_span(
        self,
    ) -> None:
        original = _evidence("original", 1_000_000, 2_000_000, "wrong")
        workspace = _completed_workspace(self.runs_root, evidence=[original])
        service = OperationService(workspace)
        record = service.execute(
            OperationRequest(
                kind=OperationKind.EVIDENCE_CORRECT,
                range=TimeRange(start_us=1_000_000, end_us=2_000_000),
                evidence_id="original",
                new_text="correct text",
                reason="人工核对",
            )
        )

        self.assertEqual(record.status, OperationStatus.COMPLETED)
        self.assertIsNotNone(record.revision_id)
        view = service.get_evidence()
        self.assertEqual(len(view.evidence), 1)
        corrected = view.evidence[0]
        self.assertEqual(corrected.raw_text, "correct text")
        self.assertEqual(corrected.provider, "user")
        self.assertEqual(corrected.confidence_kind, "human_confirmed")
        self.assertEqual(corrected.correction_of, "original")

        revision_path = (
            workspace.root
            / "revisions"
            / "evidence"
            / f"{record.revision_id}.json"
        )
        revision = EvidenceRevision.model_validate_json(
            revision_path.read_text(encoding="utf-8")
        )
        self.assertEqual({item.id for item in revision.all_evidence}, {"original", corrected.id})
        base_payload = json.loads(
            (workspace.root / "evidence" / "timeline.json").read_text(encoding="utf-8")
        )
        self.assertEqual(base_payload["evidence"][0]["raw_text"], "wrong")

    def test_execution_failure_is_recorded_without_activating_revision(self) -> None:
        workspace = _completed_workspace(
            self.runs_root,
            evidence=[_evidence("old", 1_000_000, 2_000_000, "old")],
        )

        def fake_extract(
            extraction: AudioExtractionResult,
            output_path: str | Path,
            *,
            start_us: int,
            end_us: int,
            ffmpeg_path: str = "ffmpeg",
        ) -> AudioExtractionResult:
            del ffmpeg_path
            destination = Path(output_path)
            destination.write_bytes(b"window")
            return AudioExtractionResult(
                input_path=extraction.output_path,
                output_path=str(destination),
                audio_stream_index=0,
                source_stream_start_us=start_us,
                timeline_origin_us=0,
                output_time_zero_canonical_us=start_us,
                duration_us=end_us - start_us,
            )

        def failing_transcriber(*args: Any, **kwargs: Any) -> ASREvidenceResult:
            del args, kwargs
            raise RuntimeError("provider token=must-not-leak")

        service = OperationService(
            workspace,
            asr_backend=PlaceholderAsrBackend(),
            audio_window_extractor=fake_extract,
            asr_transcriber=failing_transcriber,
        )
        record = service.execute(
            OperationRequest(
                kind=OperationKind.ASR_RETRANSCRIBE,
                range=TimeRange(start_us=1_000_000, end_us=2_000_000),
            )
        )

        self.assertEqual(record.status, OperationStatus.FAILED)
        self.assertEqual(record.error_type, "RuntimeError")
        self.assertNotIn("must-not-leak", record.model_dump_json())
        self.assertIsNone(record.revision_id)
        self.assertEqual(service.get_evidence().revision_id, None)
        revision_index = workspace.root / "revisions" / "evidence" / "index.json"
        self.assertFalse(revision_index.exists())
        self.assertEqual(service.list_operations()[0].status, OperationStatus.FAILED)

    def test_missing_model_backends_are_explicit_conflicts(self) -> None:
        workspace = _completed_workspace(
            self.runs_root,
            evidence=[_evidence("old", 1_000_000, 2_000_000, "old")],
        )
        service = OperationService(workspace)
        with self.assertRaisesRegex(OperationConflictError, "ASR backend"):
            service.execute(
                OperationRequest(
                    kind=OperationKind.ASR_RETRANSCRIBE,
                    range=TimeRange(start_us=1_000_000, end_us=2_000_000),
                )
            )
        with self.assertRaisesRegex(OperationConflictError, "OCR backend"):
            service.execute(
                OperationRequest(
                    kind=OperationKind.VISION_RESCAN,
                    range=TimeRange(start_us=1_000_000, end_us=2_000_000),
                    run_ocr=True,
                )
            )
        self.assertEqual(list((workspace.root / "operations").iterdir()), [])

    def test_unverified_path_is_rejected_before_asr_execution(self) -> None:
        workspace = _completed_workspace(
            self.runs_root,
            evidence=[_evidence("old", 1_000_000, 2_000_000, "old")],
        )
        extraction_path = workspace.root / "audio" / "extraction.json"
        payload = json.loads(extraction_path.read_text(encoding="utf-8"))
        payload["extraction"]["output_path"] = str(Path(self.temporary.name) / "outside.wav")
        extraction_path.write_text(json.dumps(payload), encoding="utf-8")

        service = OperationService(
            workspace,
            asr_backend=PlaceholderAsrBackend(),
        )
        with self.assertRaises(OperationConflictError):
            service.execute(
                OperationRequest(
                    kind=OperationKind.ASR_RETRANSCRIBE,
                    range=TimeRange(start_us=1_000_000, end_us=2_000_000),
                )
            )
        self.assertEqual(list((workspace.root / "operations").iterdir()), [])

    def test_operations_api_requires_token_and_revision_id_is_path_safe(self) -> None:
        context = ApiContext(
            self.temporary.name,
            token="test-token",
            model_registry=ModelRegistry.with_local_defaults(),
            secret_store=KeyringSecretStore(InMemoryKeyring()),
        )
        workspace = _completed_workspace(
            context.runs_root,
            evidence=[_evidence("original", 1_000_000, 2_000_000, "wrong")],
        )
        client = TestClient(create_app(context))
        self.addCleanup(client.close)
        path = f"/api/runs/{workspace.manifest.run_id}/operations"
        request = {
            "kind": "evidence_correct",
            "range": {"start_us": 1_000_000, "end_us": 2_000_000},
            "evidence_id": "original",
            "new_text": "right",
        }

        self.assertEqual(client.post(path, json=request).status_code, 401)
        created = client.post(
            path,
            headers={"X-Video2Notes-Token": "test-token"},
            json=request,
        )
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["status"], "completed")
        listed = client.get(
            path,
            headers={"X-Video2Notes-Token": "test-token"},
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)
        escaped = client.get(
            f"/api/runs/{workspace.manifest.run_id}/evidence",
            headers={"X-Video2Notes-Token": "test-token"},
            params={"revision": "../manifest"},
        )
        self.assertEqual(escaped.status_code, 404)


def _completed_workspace(
    runs_root: Path,
    *,
    evidence: list[EvidenceSpan],
) -> RunWorkspace:
    workspace = RunWorkspace.create(
        runs_root,
        source=SourceDescriptor(kind="local", locator="fixture.mp4"),
        profile="balanced",
    )
    run_id = workspace.manifest.run_id
    evidence = [
        item.model_copy(update={"run_id": run_id})
        for item in evidence
    ]

    media_path = workspace.root / "media" / "fixture.mp4"
    media_path.write_bytes(b"not-decoded-by-fakes")
    with workspace.stage("source.acquire", stage_version="test") as stage:
        stage.add_output(media_path, kind=ArtifactKind.MEDIA)

    media_manifest = MediaManifest(
        source_path=str(media_path),
        source_sha256=sha256_file(media_path),
        file_size=media_path.stat().st_size,
        duration_us=10_000_000,
        timeline_origin_us=0,
        streams=[
            MediaStream(
                index=0,
                codec_type="video",
                codec_name="fixture",
                time_base=Rational(numerator=1, denominator=1_000_000),
                width=12,
                height=8,
            ),
            MediaStream(
                index=1,
                codec_type="audio",
                codec_name="pcm_s16le",
                time_base=Rational(numerator=1, denominator=16_000),
                start_time_us=0,
                duration_us=10_000_000,
                sample_rate=16_000,
                channels=1,
            ),
        ],
    )
    media_manifest_path = workspace.root / "media" / "media-manifest.json"
    media_manifest_path.write_text(
        media_manifest.model_dump_json(),
        encoding="utf-8",
    )
    with workspace.stage("media.probe", stage_version="test") as stage:
        stage.add_output(media_manifest_path, kind=ArtifactKind.MEDIA)

    audio_path = workspace.root / "audio" / "audio-16k-mono.wav"
    audio_path.write_bytes(b"fixture-audio")
    extraction = AudioExtractionResult(
        input_path=str(media_path),
        output_path=str(audio_path),
        audio_stream_index=1,
        source_stream_start_us=0,
        timeline_origin_us=0,
        output_time_zero_canonical_us=0,
        duration_us=10_000_000,
    )
    extraction_path = workspace.root / "audio" / "extraction.json"
    extraction_path.write_text(
        json.dumps({"extraction": extraction.model_dump(mode="json")}),
        encoding="utf-8",
    )
    with workspace.stage("audio.extract", stage_version="test") as stage:
        stage.add_output(extraction_path, kind=ArtifactKind.AUDIO)
        stage.add_output(audio_path, kind=ArtifactKind.AUDIO)

    timeline_path = workspace.root / "evidence" / "timeline.json"
    timeline_path.write_text(
        json.dumps(
            {"evidence": [item.model_dump(mode="json") for item in evidence]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with workspace.stage("evidence.fuse", stage_version="test") as stage:
        stage.add_output(timeline_path, kind=ArtifactKind.EVIDENCE)
    workspace.set_status(RunStatus.COMPLETED)
    return workspace


def _evidence(
    evidence_id: str,
    start_us: int,
    end_us: int,
    text: str,
    *,
    modality: EvidenceModality = EvidenceModality.ASR,
) -> EvidenceSpan:
    return EvidenceSpan(
        id=evidence_id,
        run_id="placeholder",
        modality=modality,
        start_us=start_us,
        end_us=end_us,
        raw_text=text,
        normalized_text=text,
        provider="fixture",
        model="fixture",
        version="1",
    )
