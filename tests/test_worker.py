from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from jm_qqbot.worker import count_album_pages, find_images, save_as_jpeg


class _Photo:
    def __init__(self, count: int):
        self.page_arr = list(range(count))


class _Client:
    def get_photo_detail(self, photo_id: str, fetch_album: bool = False):
        return _Photo({"101": 12, "102": 18, "999": 20}[str(photo_id)])


class _Album:
    page_count = 0
    episode_list = [("101", "一"), ("102", "二")]


class WorkerTests(unittest.TestCase):
    def test_counts_chapters(self) -> None:
        self.assertEqual(count_album_pages(_Client(), _Album(), "999"), 30)

    def test_natural_image_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for relative in ("10/2.webp", "2/10.webp", "2/1.webp"):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"x")
            self.assertEqual(
                [path.relative_to(root).as_posix() for path in find_images(root)],
                ["2/1.webp", "2/10.webp", "10/2.webp"],
            )

    def test_transparent_webp_to_jpeg(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.webp"
            target = Path(temp) / "target.jpg"
            Image.new("RGBA", (10, 20), (255, 0, 0, 128)).save(source, "WEBP")
            save_as_jpeg(source, target, 85)
            with Image.open(target) as image:
                self.assertEqual((image.format, image.mode, image.size), ("JPEG", "RGB", (10, 20)))


if __name__ == "__main__":
    unittest.main()
