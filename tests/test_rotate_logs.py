import os
import tempfile
import unittest
from pathlib import Path

from scripts.rotate_logs import rotate_if_large


class RotateLogsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.log_path = Path(self.directory.name) / "web.log"

    def write_log(self, size_bytes):
        self.log_path.write_bytes(b"x" * size_bytes)

    def test_small_log_is_left_alone(self):
        self.write_log(1024)

        result = rotate_if_large(self.log_path, max_bytes=4096, generations=3)

        self.assertIsNone(result)
        self.assertTrue(self.log_path.exists())

    def test_missing_log_is_ignored(self):
        result = rotate_if_large(self.log_path, max_bytes=10, generations=3)

        self.assertIsNone(result)
        self.assertFalse(self.log_path.exists())

    def test_large_log_becomes_the_first_generation(self):
        self.write_log(8192)

        result = rotate_if_large(self.log_path, max_bytes=4096, generations=3)

        self.assertEqual(result, self.log_path)
        self.assertTrue(self.log_path.exists())
        self.assertEqual(self.log_path.read_bytes(), b"")
        self.assertEqual(Path(f"{self.log_path}.1").stat().st_size, 8192)

    def test_generations_shift_and_oldest_is_dropped(self):
        for generation in (1, 2, 3):
            Path(f"{self.log_path}.{generation}").write_bytes(
                f"gen{generation}".encode()
            )
        self.write_log(8192)

        rotate_if_large(self.log_path, max_bytes=4096, generations=3)

        # 가장 오래된 .3(gen3)은 삭제되고 나머지는 한 칸씩 민다.
        self.assertFalse(Path(f"{self.log_path}.4").exists())
        self.assertEqual(Path(f"{self.log_path}.3").read_bytes(), b"gen2")
        self.assertEqual(Path(f"{self.log_path}.2").read_bytes(), b"gen1")
        self.assertEqual(Path(f"{self.log_path}.1").stat().st_size, 8192)
        self.assertEqual(self.log_path.read_bytes(), b"")

    def test_rotation_never_keeps_more_generations_than_requested(self):
        for generation in (1, 2, 3, 4, 5):
            Path(f"{self.log_path}.{generation}").write_bytes(b"old")
        self.write_log(8192)

        rotate_if_large(self.log_path, max_bytes=4096, generations=2)

        remaining = sorted(
            path.name for path in self.log_path.parent.iterdir()
        )
        self.assertEqual(remaining, ["web.log", "web.log.1", "web.log.2"])

    def test_exact_threshold_is_not_rotated(self):
        self.write_log(4096)

        result = rotate_if_large(self.log_path, max_bytes=4096, generations=3)

        self.assertIsNone(result)
        self.assertEqual(self.log_path.stat().st_size, 4096)
        self.assertFalse(Path(f"{self.log_path}.1").exists())

    def test_open_descriptor_continues_writing_to_current_log(self):
        self.write_log(8192)
        descriptor = os.open(self.log_path, os.O_WRONLY | os.O_APPEND)
        try:
            rotate_if_large(self.log_path, max_bytes=4096, generations=3)
            os.write(descriptor, b"after\n")
        finally:
            os.close(descriptor)

        self.assertEqual(self.log_path.read_bytes(), b"after\n")
        self.assertEqual(
            Path(f"{self.log_path}.1").read_bytes(), b"x" * 8192
        )


if __name__ == "__main__":
    unittest.main()
