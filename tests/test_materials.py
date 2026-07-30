from __future__ import annotations

import tempfile
import unittest
from io import BytesIO

from PIL import Image

from video2notes.artifacts import RunWorkspace
from video2notes.domain import SourceDescriptor
from video2notes.materials import MaterialStatus, MaterialStore, TextMaterialRequest


def png_bytes() -> bytes:
    stream = BytesIO()
    Image.new("RGB", (12, 8), (24, 166, 111)).save(stream, format="PNG")
    return stream.getvalue()


class MaterialStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = RunWorkspace.create(
            self.temporary.name,
            run_id="run-materials",
            source=SourceDescriptor(kind="local", locator="video.mp4"),
            profile="balanced",
        )
        self.store = MaterialStore(self.workspace)

    def test_text_and_image_are_bound_to_run_with_content_hashes(self) -> None:
        text = self.store.add_text(
            TextMaterialRequest(
                title="评论区补充资料",
                content="一条需要和视频证据一起整理的资料。",
                start_us=10_000_000,
                end_us=20_000_000,
            )
        )
        image = self.store.add_file(
            filename="../../reference.png",
            content_type="image/png",
            content=png_bytes(),
            title="外部架构图",
        )
        self.assertEqual(len(self.store.list()), 2)
        self.assertTrue(self.workspace.verify_ref(text.artifact))
        self.assertTrue(self.workspace.verify_ref(image.artifact))
        self.assertEqual(image.original_name, "reference.png")
        self.assertTrue(image.artifact.relative_path.startswith("supporting/files/"))

    def test_invalid_image_and_reversed_range_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "valid image"):
            self.store.add_file(
                filename="fake.png",
                content_type="image/png",
                content=b"not-an-image",
            )
        with self.assertRaisesRegex(ValueError, "greater"):
            TextMaterialRequest(
                title="bad",
                content="bad",
                start_us=20,
                end_us=10,
            )

    def test_delete_is_soft_and_history_remains_queryable(self) -> None:
        material = self.store.add_text(
            TextMaterialRequest(title="资料", content="保留历史")
        )
        deleted = self.store.delete(material.id)
        self.assertEqual(deleted.status, MaterialStatus.DELETED)
        self.assertEqual(self.store.list(), [])
        self.assertEqual(len(self.store.list(include_deleted=True)), 1)
