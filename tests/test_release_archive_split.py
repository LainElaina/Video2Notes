from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "split_release_archive.ps1"
POWERSHELL = shutil.which("powershell")


@unittest.skipUnless(POWERSHELL, "Windows PowerShell is required")
class ReleaseArchiveSplitTests(unittest.TestCase):
    def run_script(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(POWERSHELL),
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SCRIPT_PATH),
                *arguments,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    def test_split_is_deterministic_and_manifest_verifies_reassembly(self) -> None:
        payload = bytes(range(32)) + b"Video2Notes-release-parts"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "runtime.zip"
            output = root / "parts"
            archive.write_bytes(payload)

            first = self.run_script(
                "-ArchivePath",
                str(archive),
                "-OutputDirectory",
                str(output),
                "-MaxPartBytes",
                "13",
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)

            part_paths = sorted(output.glob("runtime.zip.part[0-9][0-9][0-9]"))
            self.assertEqual(
                [path.name for path in part_paths],
                [
                    "runtime.zip.part001",
                    "runtime.zip.part002",
                    "runtime.zip.part003",
                    "runtime.zip.part004",
                    "runtime.zip.part005",
                ],
            )
            self.assertEqual([path.stat().st_size for path in part_paths], [13, 13, 13, 13, 5])
            self.assertEqual(b"".join(path.read_bytes() for path in part_paths), payload)

            manifest_path = output / "runtime.zip.parts.json"
            first_manifest_bytes = manifest_path.read_bytes()
            manifest = json.loads(first_manifest_bytes)
            archive_hash = hashlib.sha256(payload).hexdigest()
            self.assertEqual(manifest["schema"], 1)
            self.assertEqual(manifest["archive"]["file_name"], archive.name)
            self.assertEqual(manifest["archive"]["size_bytes"], len(payload))
            self.assertEqual(manifest["archive"]["sha256"], archive_hash)
            self.assertEqual(manifest["max_part_size_bytes"], 13)
            self.assertEqual(manifest["part_count"], 5)
            self.assertEqual(
                [part["sha256"] for part in manifest["parts"]],
                [hashlib.sha256(path.read_bytes()).hexdigest() for path in part_paths],
            )
            self.assertEqual(
                manifest["reassembly"],
                {
                    "verified": True,
                    "size_bytes": len(payload),
                    "sha256": archive_hash,
                },
            )

            collision = self.run_script(
                "-ArchivePath",
                str(archive),
                "-OutputDirectory",
                str(output),
                "-MaxPartBytes",
                "13",
            )
            self.assertNotEqual(collision.returncode, 0)
            self.assertIn("-Overwrite", collision.stdout + collision.stderr)

            overwrite = self.run_script(
                "-ArchivePath",
                str(archive),
                "-OutputDirectory",
                str(output),
                "-MaxPartBytes",
                "13",
                "-Overwrite",
            )
            self.assertEqual(overwrite.returncode, 0, overwrite.stdout + overwrite.stderr)
            self.assertEqual(manifest_path.read_bytes(), first_manifest_bytes)

            verify = self.run_script(
                "-Mode",
                "Verify",
                "-ArchivePath",
                str(archive),
                "-OutputDirectory",
                str(output),
            )
            self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
            self.assertIn("parts verified", verify.stdout.lower())

    def test_verify_rejects_a_tampered_part(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "runtime.zip"
            output = root / "parts"
            archive.write_bytes(b"0123456789abcdef")

            split = self.run_script(
                "-ArchivePath",
                str(archive),
                "-OutputDirectory",
                str(output),
                "-MaxPartBytes",
                "8",
            )
            self.assertEqual(split.returncode, 0, split.stdout + split.stderr)

            second_part = output / "runtime.zip.part002"
            second_part.write_bytes(b"X" + second_part.read_bytes()[1:])
            verify = self.run_script(
                "-Mode",
                "Verify",
                "-ArchivePath",
                str(archive),
                "-OutputDirectory",
                str(output),
            )
            self.assertNotEqual(verify.returncode, 0)
            self.assertIn("sha-256 does not match", (verify.stdout + verify.stderr).lower())


if __name__ == "__main__":
    unittest.main()
