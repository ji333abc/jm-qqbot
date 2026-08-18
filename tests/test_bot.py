from __future__ import annotations

import tempfile
import sys
import types
import unittest
from pathlib import Path

try:
    import botpy  # noqa: F401
except ImportError:
    botpy_module = types.ModuleType("botpy")
    botpy_module.Client = object
    botpy_module.Intents = object
    message_module = types.ModuleType("botpy.message")
    message_module.GroupMessage = object
    sys.modules["botpy"] = botpy_module
    sys.modules["botpy.message"] = message_module

from jm_qqbot.bot import Settings, _estimate_seconds, parse_album_ids


class CommandTests(unittest.TestCase):
    def test_single_and_batch(self) -> None:
        self.assertEqual(parse_album_ids("JM 123"), ["123"])
        self.assertEqual(parse_album_ids("jm下载 222 111 222"), ["222", "111"])

    def test_rejects_malformed_input(self) -> None:
        self.assertEqual(parse_album_ids("JM 123,456"), [])
        self.assertEqual(parse_album_ids("播放 123"), [])


class EstimateTests(unittest.TestCase):
    def test_default_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(timing_path=Path(temp) / "timing.json")
            self.assertEqual(_estimate_seconds(settings, 410), 220)


if __name__ == "__main__":
    unittest.main()
