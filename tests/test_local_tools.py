from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video2notes.components.local_tools import (
    LocalToolManager,
    LocalToolPathError,
)


class LocalToolManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manager = LocalToolManager(self.root)

    def test_manual_executable_binding_is_localized_and_persisted(self) -> None:
        selected = self.root / "external" / "ffmpeg.exe"
        selected.parent.mkdir()
        selected.write_bytes(b"fixture")

        with patch(
            "video2notes.components.local_tools._run_version",
            return_value=("7.1.1", "ffmpeg version 7.1.1", None),
        ):
            result = self.manager.bind("tool.ffmpeg", selected)

        self.assertTrue(result.bound)
        self.assertTrue(result.compatible)
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.version, "7.1.1")
        self.assertEqual(result.path, str(selected.resolve()))
        self.assertIn("视频处理", result.display_name_zh)

        restored = LocalToolManager(self.root)
        with patch(
            "video2notes.components.local_tools._run_version",
            return_value=("7.1.1", "ffmpeg version 7.1.1", None),
        ):
            inventory = restored.inventory()
        binding = inventory.bindings["tool.ffmpeg"]
        self.assertEqual(binding.path, str(selected.resolve()))
        self.assertTrue(
            next(item for item in inventory.tools if item.dependency_id == "tool.ffmpeg").bound
        )

    def test_directory_binding_selects_the_expected_program(self) -> None:
        directory = self.root / "ffmpeg-bin"
        directory.mkdir()
        executable = directory / "ffprobe.exe"
        executable.write_bytes(b"fixture")

        with patch(
            "video2notes.components.local_tools._run_version",
            return_value=("7.1.1", "ffprobe version 7.1.1", None),
        ):
            result = self.manager.bind("tool.ffprobe", directory)

        self.assertEqual(result.path, str(executable.resolve()))

    def test_python_module_directory_can_be_bound_without_importing_it(self) -> None:
        site_packages = self.root / "site-packages"
        module = site_packages / "faster_whisper" / "__init__.py"
        module.parent.mkdir(parents=True)
        module.write_text("raise RuntimeError('must not be imported')\n", encoding="utf-8")

        result = self.manager.bind("asr.faster_whisper", site_packages)

        self.assertTrue(result.compatible)
        self.assertTrue(result.bound)
        self.assertEqual(result.path, str(site_packages.resolve()))
        self.assertIn("Python 模块", result.detail_zh or "")

    def test_unbind_never_removes_the_selected_program(self) -> None:
        selected = self.root / "external" / "ffmpeg.exe"
        selected.parent.mkdir()
        selected.write_bytes(b"fixture")
        with patch(
            "video2notes.components.local_tools._run_version",
            return_value=("7.1.1", "ffmpeg version 7.1.1", None),
        ):
            self.manager.bind("tool.ffmpeg", selected)

        self.assertTrue(self.manager.unbind("tool.ffmpeg"))
        self.assertTrue(selected.is_file())
        self.assertFalse(self.manager.unbind("tool.ffmpeg"))

    def test_missing_or_mismatched_program_is_rejected(self) -> None:
        with self.assertRaises(LocalToolPathError):
            self.manager.bind("tool.ffmpeg", self.root / "missing.exe")

        wrong = self.root / "notepad.exe"
        wrong.write_bytes(b"fixture")
        with self.assertRaisesRegex(LocalToolPathError, "不是预期"):
            self.manager.bind("tool.ffmpeg", wrong)


if __name__ == "__main__":
    unittest.main()
