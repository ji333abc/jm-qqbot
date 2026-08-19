"""QQ 官方群机器人：接收 JM 命令、下载打包并上传文件。"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread

import botpy
from botpy.message import GroupMessage

logger = logging.getLogger("JMQQBot")
_seen_messages: dict[str, float] = {}
_seen_lock = Lock()
_job_lock = Lock()
_progress_lock = Lock()
_tasks: set[asyncio.Task] = set()
_PROGRESS_COMMANDS = {"jm进度", "jm状态", "查询当前下载进度", "查询下载进度", "下载进度"}
_IMAGE_SUFFIXES = {".webp", ".jpg", ".jpeg", ".png", ".bmp"}


@dataclass(slots=True)
class JobProgress:
    album_id: str
    total_pages: int | None
    job_dir: Path
    group_openid: str
    batch_index: int = 1
    batch_total: int = 1
    downloaded_pages: int = 0
    phase: str = "downloading"


_active_progress: JobProgress | None = None


def _csv_set(name: str) -> set[str]:
    return {item.strip() for item in os.getenv(name, "").split(",") if item.strip()}


@dataclass(slots=True)
class Settings:
    app_id: str = field(default_factory=lambda: os.getenv("QQBOT_APP_ID", "").strip())
    app_secret: str = field(default_factory=lambda: os.getenv("QQBOT_APP_SECRET", "").strip())
    allowed_groups: set[str] = field(default_factory=lambda: _csv_set("QQBOT_ALLOWED_GROUP_OPENIDS"))
    allowed_users: set[str] = field(default_factory=lambda: _csv_set("QQBOT_JM_ALLOWED_USER_OPENIDS"))
    batch_max_items: int = field(default_factory=lambda: max(1, int(os.getenv("QQBOT_JM_BATCH_MAX_ITEMS", "3"))))
    max_bytes: int = field(default_factory=lambda: int(os.getenv("QQBOT_JM_MAX_BYTES", str(80 * 1024 * 1024))))
    download_timeout: int = field(default_factory=lambda: int(os.getenv("QQBOT_JM_TIMEOUT_SECONDS", "1200")))
    upload_timeout: int = field(default_factory=lambda: int(os.getenv("QQBOT_JM_UPLOAD_TIMEOUT_SECONDS", "900")))
    inspect_timeout: int = field(default_factory=lambda: int(os.getenv("QQBOT_JM_INSPECT_TIMEOUT_SECONDS", "30")))
    failure_retain: int = field(default_factory=lambda: int(os.getenv("QQBOT_JM_FAILURE_RETAIN_SECONDS", "1800")))
    temp_root: Path = field(default_factory=lambda: Path(os.getenv("QQBOT_JM_TEMP_ROOT", "data/jm-tasks")).resolve())
    timing_path: Path = field(default_factory=lambda: Path(os.getenv("QQBOT_JM_TIMING_PATH", "data/jm-timing.json")).resolve())
    node: str = field(default_factory=lambda: os.getenv("QQBOT_JM_NODE", "node").strip())
    uploader: Path = field(
        default_factory=lambda: Path(
            os.getenv(
                "QQBOT_JM_UPLOADER",
                str(Path(__file__).resolve().parents[1] / "uploader" / "uploader.mjs"),
            )
        ).resolve()
    )

    def validate(self) -> None:
        missing = [
            name
            for name, value in (("QQBOT_APP_ID", self.app_id), ("QQBOT_APP_SECRET", self.app_secret))
            if not value
        ]
        if missing:
            raise SystemExit("缺少环境变量: " + ", ".join(missing))
        if self.max_bytes <= 0 or self.max_bytes > 100 * 1024 * 1024:
            raise SystemExit("QQBOT_JM_MAX_BYTES 必须在 1 到 104857600 之间")
        if not self.uploader.is_file():
            raise SystemExit(f"QQ 上传器不存在: {self.uploader}")
        if not Path(self.node).is_file() and shutil.which(self.node) is None:
            raise SystemExit(f"Node.js 不存在: {self.node}")
        sdk = self.uploader.parent / "node_modules" / "@tencent-connect" / "qqbot-nodejs" / "package.json"
        if not sdk.is_file():
            raise SystemExit("上传器依赖未安装；请在 uploader 目录执行 npm ci")


def parse_album_ids(command: str) -> list[str]:
    match = re.fullmatch(
        r"(?:jm|JM|jm下载|JM下载)\s*(\d{1,12}(?:\s+\d{1,12})*)",
        str(command or "").strip(),
    )
    return list(dict.fromkeys(re.findall(r"\d{1,12}", match.group(1)))) if match else []


def is_progress_command(command: str) -> bool:
    return re.sub(r"\s+", "", str(command or "")).lower() in _PROGRESS_COMMANDS


def _progress_percent(downloaded_pages: int, total_pages: int | None, phase: str) -> int | None:
    if phase == "completed":
        return 100
    if phase in {"uploading", "packaging"}:
        return 99
    if not total_pages or total_pages <= 0:
        return None
    return min(99, max(0, downloaded_pages) * 99 // total_pages)


def _progress_bar(percent: int | None, width: int = 20) -> str:
    if percent is None:
        return "░" * width
    filled = min(width, max(0, percent) * width // 100)
    return "█" * filled + "░" * (width - filled)


def _count_downloaded_pages(job_dir: Path) -> int:
    image_dir = job_dir / "images"
    try:
        return sum(
            1
            for item in image_dir.rglob("*")
            if item.is_file() and item.suffix.lower() in _IMAGE_SUFFIXES
        )
    except OSError:
        return 0


def _read_worker_progress(job_dir: Path) -> dict:
    try:
        data = json.loads((job_dir / "progress.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _set_active_progress(progress: JobProgress | None) -> None:
    global _active_progress
    with _progress_lock:
        _active_progress = progress


def _get_active_progress() -> JobProgress | None:
    with _progress_lock:
        return _active_progress


def _clear_active_progress(progress: JobProgress) -> None:
    global _active_progress
    with _progress_lock:
        if _active_progress is progress:
            _active_progress = None


def _is_duplicate(message_id: str) -> bool:
    now = time.monotonic()
    with _seen_lock:
        for key in [key for key, value in _seen_messages.items() if now - value > 600]:
            _seen_messages.pop(key, None)
        if message_id in _seen_messages:
            return True
        _seen_messages[message_id] = now
        return False


def _inspect_album(settings: Settings, album_id: str) -> int:
    result = subprocess.run(
        [sys.executable, "-m", "jm_qqbot.worker", "--inspect", album_id],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=settings.inspect_timeout,
        check=False,
    )
    for line in reversed(result.stdout.splitlines()):
        if line.startswith("JM_METADATA="):
            page_count = int(json.loads(line.removeprefix("JM_METADATA=")).get("page_count") or 0)
            if page_count > 0:
                return page_count
    detail = (result.stderr or result.stdout).strip().replace("\n", " ")[-500:]
    raise RuntimeError(detail or f"JM 元数据查询失败（退出码 {result.returncode}）")


def _load_samples(settings: Settings) -> list[dict]:
    try:
        data = json.loads(settings.timing_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in data.get("samples", []) if isinstance(item, dict)][-20:]


def _estimate_seconds(settings: Settings, page_count: int) -> int:
    samples = _load_samples(settings)
    per_page = [
        float(item.get("total_seconds", 0)) / float(item.get("page_count", 0))
        for item in samples
        if float(item.get("page_count", 0)) > 0
    ]
    observed = sorted(per_page)[len(per_page) // 2] if per_page else 0.5
    confidence = min(1.0, sum(int(item.get("page_count") or 0) for item in samples) / 200)
    seconds = 15 + page_count * (0.5 * (1 - confidence) + observed * confidence)
    return max(30, int(math.ceil(seconds / 10) * 10))


def _record_timing(settings: Settings, sample: dict) -> None:
    samples = _load_samples(settings)
    samples.append(sample)
    settings.timing_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = settings.timing_path.with_suffix(settings.timing_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"samples": samples[-20:]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(settings.timing_path)


def _run_download(settings: Settings, album_id: str, password: str, job_dir: Path) -> tuple[Path, dict]:
    job_dir.mkdir(parents=True, exist_ok=False)
    log_path = job_dir / "download.log"
    environment = os.environ.copy()
    environment["JM_ZIP_PASSWORD"] = password
    with log_path.open("w", encoding="utf-8") as log_file:
        result = subprocess.run(
            [sys.executable, "-m", "jm_qqbot.worker", album_id, str(job_dir)],
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            env=environment,
            timeout=settings.download_timeout,
            check=False,
        )
    if result.returncode != 0:
        detail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        raise RuntimeError(f"下载进程退出码 {result.returncode}" + (f"：{detail}" if detail else ""))
    archive = job_dir / "archives" / f"JM{album_id}.zip"
    if not archive.is_file():
        raise RuntimeError("下载完成但未找到 ZIP 文件")
    try:
        metadata = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        metadata = {}
    return archive, metadata


class UploadError(RuntimeError):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


async def _pump_stderr(stream: asyncio.StreamReader) -> None:
    while line := await stream.readline():
        text = line.decode("utf-8", errors="replace").strip()
        if text:
            logger.info("Uploader: %s", text)


async def _upload(
    settings: Settings,
    archive: Path,
    group_openid: str,
    message_id: str,
    display_name: str,
) -> dict:
    process = await asyncio.create_subprocess_exec(
        settings.node,
        str(settings.uploader),
        "--group-openid",
        group_openid,
        "--msg-id",
        message_id,
        "--file",
        str(archive.resolve()),
        "--name",
        display_name,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=os.environ.copy(),
    )
    assert process.stdout is not None and process.stderr is not None
    stdout_task = asyncio.create_task(process.stdout.read())
    stderr_task = asyncio.create_task(_pump_stderr(process.stderr))
    try:
        await asyncio.wait_for(process.wait(), timeout=settings.upload_timeout)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise UploadError("timeout", f"QQ 文件上传超过 {settings.upload_timeout} 秒") from exc
    finally:
        stdout = await stdout_task
        await stderr_task
    output = [line.strip() for line in stdout.decode(errors="replace").splitlines() if line.strip()]
    if not output:
        raise UploadError("api", f"QQ 上传器未返回结果（退出码 {process.returncode}）")
    try:
        result = json.loads(output[-1])
    except ValueError as exc:
        raise UploadError("api", "QQ 上传器返回了无效结果") from exc
    if process.returncode != 0 or result.get("ok") is not True:
        raise UploadError(str(result.get("errorType") or "api"), str(result.get("message") or "上传失败")[-500:])
    return result


async def _cleanup_later(path: Path, delay: int) -> None:
    await asyncio.sleep(delay)
    await asyncio.to_thread(shutil.rmtree, path, True)


class JMClient(botpy.Client):
    def __init__(self, settings: Settings, **kwargs) -> None:
        super().__init__(**kwargs)
        self.settings = settings

    async def on_ready(self):
        logger.info("QQ 机器人已上线: %s", self.robot.name)
        if not self.settings.allowed_groups:
            logger.warning("未配置群白名单，机器人所在的所有群都可提交任务")

    async def reply(self, message: GroupMessage, content: str, msg_seq: int = 1) -> None:
        payload = {
            "group_openid": message.group_openid,
            "msg_type": 0,
            "msg_id": message.id,
            "msg_seq": msg_seq,
            "content": content[:1000],
        }
        try:
            await message._api.post_group_message(**payload)
        except Exception as exc:
            normalized = str(exc).replace(" ", "").lower()
            if "msgid" not in normalized or "过期" not in normalized:
                raise
            payload.pop("msg_id")
            payload.pop("msg_seq")
            await message._api.post_group_message(**payload)

    async def reply_progress(self, message: GroupMessage) -> None:
        progress = _get_active_progress()
        if progress is None:
            if _job_lock.locked():
                await self.reply(message, "JM 任务正在准备，尚未开始下载")
            else:
                await self.reply(message, "当前没有正在运行的 JM 下载任务")
            return
        if progress.group_openid != str(message.group_openid):
            await self.reply(message, "当前群没有正在运行的 JM 下载任务")
            return

        counted_pages, worker_progress = await asyncio.gather(
            asyncio.to_thread(_count_downloaded_pages, progress.job_dir),
            asyncio.to_thread(_read_worker_progress, progress.job_dir),
        )
        downloaded_pages = max(
            counted_pages,
            progress.downloaded_pages,
            int(worker_progress.get("downloaded_pages") or 0),
        )
        archive_ready = (progress.job_dir / "archives" / f"JM{progress.album_id}.zip").is_file()
        phase = progress.phase
        if phase == "downloading" and worker_progress.get("download_complete") is True:
            phase = "packaging"
        if phase == "downloading" and progress.total_pages and downloaded_pages >= progress.total_pages:
            phase = "packaging"
        if archive_ready and phase == "downloading":
            phase = "packaging"
        percent = _progress_percent(downloaded_pages, progress.total_pages, phase)
        display_downloaded = progress.total_pages if percent == 99 and progress.total_pages else downloaded_pages
        percent_text = f"{percent}%" if percent is not None else "--%"
        phase_text = {
            "downloading": "正在下载",
            "packaging": "正在生成 PDF 并打包",
            "uploading": "下载与打包完成，正在上传",
            "completed": "文件已发送",
        }[phase]
        batch_text = (
            f" [{progress.batch_index}/{progress.batch_total}]" if progress.batch_total > 1 else ""
        )
        if progress.total_pages:
            pages_text = f"页数：{min(display_downloaded, progress.total_pages)}/{progress.total_pages}"
        else:
            pages_text = f"已下载：{display_downloaded} 页（总页数未知）"
        await self.reply(
            message,
            f"JM{progress.album_id}{batch_text}\n[{_progress_bar(percent)}] {percent_text}\n{pages_text}\n状态：{phase_text}",
        )

    async def run_job(
        self,
        message: GroupMessage,
        album_id: str,
        page_count: int | None,
        *,
        send_result: bool = True,
        release_lock: bool = True,
        batch_index: int = 1,
        batch_total: int = 1,
    ) -> dict:
        settings = self.settings
        job_dir = settings.temp_root / f"jm-{album_id}-{secrets.token_hex(8)}"
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
        password = "".join(secrets.choice(alphabet) for _ in range(14))
        keep_for_debug = False
        archive_created = False
        started = time.monotonic()
        progress = JobProgress(
            album_id=album_id,
            total_pages=page_count,
            job_dir=job_dir,
            group_openid=str(message.group_openid),
            batch_index=batch_index,
            batch_total=batch_total,
        )
        _set_active_progress(progress)
        try:
            archive, metadata = await asyncio.to_thread(
                _run_download, settings, album_id, password, job_dir
            )
            archive_created = True
            progress.downloaded_pages = int(metadata.get("successful_images") or 0)
            progress.phase = "uploading"
            archive_size = archive.stat().st_size
            if archive_size > settings.max_bytes:
                raise RuntimeError(
                    f"压缩包 {archive_size / 1024 / 1024:.1f} MiB，超过 {settings.max_bytes / 1024 / 1024:.0f} MiB 上限"
                )
            upload_started = time.monotonic()
            await _upload(settings, archive, str(message.group_openid), str(message.id), archive.name)
            progress.phase = "completed"
            upload_seconds = time.monotonic() - upload_started
            total_seconds = time.monotonic() - started
            timing_pages = page_count or int(metadata.get("page_count") or 0)
            if timing_pages:
                await asyncio.to_thread(
                    _record_timing,
                    settings,
                    {
                        "timestamp": int(time.time()),
                        "album_id": album_id,
                        "page_count": timing_pages,
                        "total_seconds": round(total_seconds, 3),
                        "archive_bytes": archive_size,
                        "upload_seconds": round(upload_seconds, 3),
                    },
                )
            failed_images = int(metadata.get("failed_images") or 0)
            failed_photos = int(metadata.get("failed_photos") or 0)
            if metadata.get("output_format") == "pdf":
                content_note = f"PDF（质量 {metadata.get('pdf_quality')}）"
            else:
                content_note = "原始图片（PDF 生成失败或超过大小限制）"
            warning = ""
            if failed_images or failed_photos:
                warning = f"⚠️ 有 {failed_images} 张图片、{failed_photos} 个章节下载失败"
            if send_result:
                lines = [
                    f"JM{album_id}.zip 上传完成",
                    f"内容：{content_note}",
                    f"解压密码：{password}",
                    f"实际用时：{math.ceil(total_seconds)} 秒",
                ]
                if warning:
                    lines.append(warning)
                await self.reply(message, "\n".join(lines), msg_seq=3)
            return {
                "ok": True,
                "album_id": album_id,
                "password": password,
                "seconds": math.ceil(total_seconds),
                "warning": warning,
            }
        except subprocess.TimeoutExpired:
            error_text = "下载超时，任务已停止"
        except Exception as exc:
            logger.exception("JM 任务失败: album_id=%s", album_id)
            keep_for_debug = archive_created
            if isinstance(exc, UploadError):
                error_text = {
                    "quota": "QQ 文件上传今日额度已用完，请明天再试",
                    "auth": "QQ 文件上传认证失败，请联系管理员检查配置",
                    "size": "文件超过 QQ 平台允许的大小",
                    "timeout": "QQ 文件上传超时，请稍后再试",
                    "network": "QQ 文件上传网络异常，请稍后再试",
                }.get(exc.error_type, f"QQ 文件上传失败：{exc}")
            else:
                error_text = str(exc).replace("\n", " ")[-500:]
        finally:
            _clear_active_progress(progress)
            if keep_for_debug:
                task = asyncio.create_task(_cleanup_later(job_dir, settings.failure_retain))
                _tasks.add(task)
                task.add_done_callback(_tasks.discard)
            else:
                await asyncio.to_thread(shutil.rmtree, job_dir, True)
            if release_lock:
                _job_lock.release()
        if send_result:
            await self.reply(message, f"JM{album_id} 任务失败：{error_text}", msg_seq=2)
        return {"ok": False, "album_id": album_id, "error": error_text}

    async def run_batch(self, message: GroupMessage, jobs: list[tuple[str, int | None]]) -> None:
        batch_started = time.monotonic()
        try:
            for index, (album_id, page_count) in enumerate(jobs, start=1):
                result = await self.run_job(
                    message,
                    album_id,
                    page_count,
                    send_result=False,
                    release_lock=False,
                    batch_index=index,
                    batch_total=len(jobs),
                )
                if result["ok"]:
                    lines = [
                        f"✅ [{index}/{len(jobs)}] JM{album_id}.zip 上传完成",
                        f"解压密码：{result['password']}",
                        f"本项用时：{result['seconds']} 秒",
                    ]
                    if result["warning"]:
                        lines.append(result["warning"])
                else:
                    lines = [f"❌ [{index}/{len(jobs)}] JM{album_id} 任务失败", result["error"][:300]]
                if index == len(jobs):
                    lines.extend(["批量任务已全部处理完毕", f"总用时：{math.ceil(time.monotonic() - batch_started)} 秒"])
                await self.reply(message, "\n".join(lines), msg_seq=index + 1)
        finally:
            _job_lock.release()

    async def start_jobs(self, message: GroupMessage, album_ids: list[str], requester_id: str) -> None:
        settings = self.settings
        if settings.allowed_users and requester_id not in settings.allowed_users:
            await self.reply(message, "你没有使用 JM 下载功能的权限")
            return
        if len(album_ids) > settings.batch_max_items:
            await self.reply(message, f"一次最多提交 {settings.batch_max_items} 个 JM ID")
            return
        if not _job_lock.acquire(blocking=False):
            await self.reply(message, "已有一个下载任务正在运行，请稍后再试")
            return
        try:
            jobs: list[tuple[str, int | None]] = []
            for album_id in album_ids:
                try:
                    page_count = await asyncio.to_thread(_inspect_album, settings, album_id)
                except Exception as exc:
                    logger.warning("页数查询失败: album_id=%s error=%s", album_id, exc)
                    page_count = None
                jobs.append((album_id, page_count))
            if len(jobs) == 1:
                album_id, page_count = jobs[0]
                estimate = f"\n页数：{page_count} 页\n预计约 {_estimate_seconds(settings, page_count)} 秒" if page_count else "\n预计时间：暂时无法计算"
                await self.reply(message, f"已开始下载 JM{album_id}{estimate}\n当前一次只处理一个任务")
                task = asyncio.create_task(self.run_job(message, album_id, page_count))
            else:
                lines = [f"已开始批量任务，共 {len(jobs)} 个，将按顺序处理："]
                for index, (album_id, page_count) in enumerate(jobs, start=1):
                    detail = f"{page_count} 页，约 {_estimate_seconds(settings, page_count)} 秒" if page_count else "页数未知"
                    lines.append(f"{index}. JM{album_id}（{detail}）")
                await self.reply(message, "\n".join(lines))
                task = asyncio.create_task(self.run_batch(message, jobs))
            _tasks.add(task)
            task.add_done_callback(_tasks.discard)
        except Exception:
            _job_lock.release()
            raise

    async def on_group_at_message_create(self, message: GroupMessage):
        group_openid = str(message.group_openid or "").strip()
        message_id = str(message.id or "").strip()
        command = str(message.content or "").strip()
        author = getattr(message, "author", None)
        requester_id = str(
            getattr(author, "member_openid", "") or getattr(author, "id", "") or "anonymous"
        ).strip()
        if self.settings.allowed_groups and group_openid not in self.settings.allowed_groups:
            logger.warning("忽略未授权群的命令: %s", group_openid)
            return
        if not command or _is_duplicate(message_id):
            return
        album_ids = parse_album_ids(command)
        if is_progress_command(command):
            await self.reply_progress(message)
        elif album_ids:
            await self.start_jobs(message, album_ids, requester_id)
        elif command.lower() in {"jm", "jm帮助", "jm help"}:
            await self.reply(
                message,
                "用法：@机器人 JM 作品ID [作品ID ...]"
                f"\n一次最多 {self.settings.batch_max_items} 个，例如：JM 111111 222222"
                "\n查询进度：@机器人 JM进度",
            )


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path not in {"/", "/healthz"}:
            self.send_error(404)
            return
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args) -> None:
        return


def _start_health_server() -> None:
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    Thread(target=server.serve_forever, name="health-server", daemon=True).start()
    logger.info("健康检查监听于 0.0.0.0:%s", port)


def _cleanup_stale(settings: Settings) -> None:
    cutoff = time.time() - settings.failure_retain
    for path in settings.temp_root.glob("jm-*"):
        try:
            if path.is_dir() and path.stat().st_mtime <= cutoff:
                shutil.rmtree(path)
        except OSError:
            logger.exception("清理遗留任务失败: %s", path)


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    settings = Settings()
    settings.validate()
    settings.temp_root.mkdir(parents=True, exist_ok=True)
    _cleanup_stale(settings)
    _start_health_server()
    client = JMClient(settings, intents=botpy.Intents(public_messages=True))
    client.run(appid=settings.app_id, secret=settings.app_secret)


if __name__ == "__main__":
    main()
