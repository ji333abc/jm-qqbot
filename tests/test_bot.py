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

from jm_qqbot.bot import (
    Settings,
    _estimate_seconds,
    _is_expired_reply_error,
    _progress_bar,
    _progress_percent,
    is_progress_command,
    parse_album_ids,
)


class CommandTests(unittest.TestCase):
    def test_single_and_batch(self) -> None:
        self.assertEqual(parse_album_ids("JM 123"), ["123"])
        self.assertEqual(parse_album_ids("jm下载 222 111 222"), ["222", "111"])

    def test_rejects_malformed_input(self) -> None:
        self.assertEqual(parse_album_ids("JM 123,456"), [])
        self.assertEqual(parse_album_ids("播放 123"), [])

    def test_progress_commands(self) -> None:
        self.assertTrue(is_progress_command("JM进度"))
        self.assertTrue(is_progress_command("查询 当前 下载 进度"))
        self.assertFalse(is_progress_command("JM 123"))

    def test_expired_message_variants_normalize_to_msgid(self) -> None:
        for message in ("msgid已经过期", "回复消息msg_id已过期", "MSG-ID expired"):
            self.assertTrue(_is_expired_reply_error(RuntimeError(message)))
        self.assertFalse(_is_expired_reply_error(RuntimeError("主动消息失败, 无权限")))


class ProgressTests(unittest.TestCase):
    def test_download_pages_use_ninety_nine_percent(self) -> None:
        self.assertEqual(_progress_percent(50, 100, "downloading"), 49)
        self.assertEqual(_progress_percent(100, 100, "downloading"), 99)
        self.assertEqual(_progress_percent(1, None, "downloading"), None)

    def test_packaging_and_upload_stay_at_ninety_nine_percent(self) -> None:
        self.assertEqual(_progress_percent(80, 100, "packaging"), 99)
        self.assertEqual(_progress_percent(80, 100, "uploading"), 99)
        self.assertEqual(_progress_percent(100, 100, "completed"), 100)
        self.assertEqual(_progress_bar(99), "███████████████████░")


class EstimateTests(unittest.TestCase):
    def test_default_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(timing_path=Path(temp) / "timing.json")
            self.assertEqual(_estimate_seconds(settings, 410), 220)


if __name__ == "__main__":
    unittest.main()
