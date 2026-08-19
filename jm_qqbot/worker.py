"""在隔离子进程中下载一个作品并生成加密 ZIP。"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

PDF_QUALITIES = (85, 78)
DEFAULT_MAX_ARCHIVE_BYTES = 80 * 1024 * 1024


def build_option(JmOption, image_dir: Path | None = None, *, log: bool = True):
    config = {
        "log": log,
        "download": {"cache": True, "threading": {"image": 4, "photo": 1}},
        "client": {
            "impl": "api",
            "retry_times": 5,
            "postman": {"meta_data": {"proxies": None}},
        },
    }
    if image_dir is not None:
        config["dir_rule"] = {"base_dir": str(image_dir), "rule": "Bd / Pindex"}
    return JmOption.construct(config)


def _episode_id(item) -> str:
    if isinstance(item, (list, tuple)) and item:
        return str(item[0] or "").strip()
    if isinstance(item, dict):
        return str(item.get("photo_id") or item.get("id") or item.get("album_id") or "").strip()
    return str(
        getattr(item, "photo_id", None)
        or getattr(item, "id", None)
        or getattr(item, "album_id", None)
        or ""
    ).strip()


def count_album_pages(client, album, album_id: str) -> int:
    try:
        direct_count = int(getattr(album, "page_count", 0) or 0)
    except (TypeError, ValueError):
        direct_count = 0
    if direct_count > 0:
        return direct_count

    photo_ids = list(
        dict.fromkeys(
            photo_id
            for item in (getattr(album, "episode_list", None) or [])
            if (photo_id := _episode_id(item))
        )
    ) or [str(album_id)]
    total = 0
    for photo_id in photo_ids:
        try:
            photo = client.get_photo_detail(photo_id, fetch_album=False)
        except TypeError:
            photo = client.get_photo_detail(photo_id)
        pages = getattr(photo, "page_arr", None)
        try:
            count = len(pages) if pages is not None else int(getattr(photo, "page_count", 0) or 0)
        except (TypeError, ValueError):
            count = 0
        total += max(0, count)
    return total


def inspect_album(album_id: str) -> None:
    from jmcomic import JmOption

    option = build_option(JmOption, log=False)
    client = option.new_jm_client()
    album = client.get_album_detail(album_id)
    print(
        "JM_METADATA="
        + json.dumps(
            {"page_count": count_album_pages(client, album, album_id)},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def _natural_key(path: Path, root: Path) -> tuple:
    parts: list[tuple[int, int | str]] = []
    for component in path.relative_to(root).parts:
        for token in re.split(r"(\d+)", component.lower()):
            if token:
                parts.append((0, int(token)) if token.isdigit() else (1, token))
    return tuple(parts)


def find_images(image_dir: Path) -> list[Path]:
    supported = {".webp", ".jpg", ".jpeg", ".png", ".bmp"}
    images = [
        item
        for item in image_dir.rglob("*")
        if item.is_file() and item.suffix.lower() in supported
    ]
    return sorted(images, key=lambda item: _natural_key(item, image_dir))


def save_as_jpeg(source: Path, target: Path, quality: int) -> None:
    from PIL import Image, ImageOps

    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened)
        image.load()
        if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
            rgba = image.convert("RGBA")
            rgb = Image.new("RGB", rgba.size, "white")
            rgb.paste(rgba, mask=rgba.getchannel("A"))
        else:
            rgb = image.convert("RGB")
        target.parent.mkdir(parents=True, exist_ok=True)
        rgb.save(target, format="JPEG", quality=quality, optimize=True, progressive=False)


def _build_pdf(images: list[Path], pdf_path: Path, page_dir: Path, quality: int) -> None:
    import img2pdf

    shutil.rmtree(page_dir, ignore_errors=True)
    page_dir.mkdir(parents=True, exist_ok=True)
    jpeg_pages: list[str] = []
    for index, source in enumerate(images, start=1):
        target = page_dir / f"{index:06d}.jpg"
        save_as_jpeg(source, target, quality)
        jpeg_pages.append(str(target))
    with pdf_path.open("wb") as output:
        img2pdf.convert(jpeg_pages, outputstream=output)


def _write_encrypted_zip(
    archive_path: Path,
    password: str,
    files: list[tuple[Path, str]],
) -> None:
    import pyzipper

    archive_path.unlink(missing_ok=True)
    with pyzipper.AESZipFile(
        archive_path,
        "w",
        compression=pyzipper.ZIP_DEFLATED,
        encryption=pyzipper.WZ_AES,
    ) as archive:
        archive.setpassword(password.encode("utf-8"))
        archive.setencryption(pyzipper.WZ_AES, nbits=256)
        for source, archive_name in files:
            archive.write(source, archive_name)


def create_archive(
    album_id: str,
    image_dir: Path,
    archive_dir: Path,
    password: str,
) -> tuple[Path, str, int | None, str]:
    images = find_images(image_dir)
    if not images:
        raise RuntimeError("没有找到可打包的图片")

    max_archive_bytes = int(os.environ.get("QQBOT_JM_MAX_BYTES") or DEFAULT_MAX_ARCHIVE_BYTES)
    pdf_budget = max(1, int(max_archive_bytes * 0.98))
    pdf_path = archive_dir / f"JM{album_id}.pdf"
    page_dir = archive_dir / "pdf-pages"
    conversion_error = ""
    for quality in PDF_QUALITIES:
        try:
            pdf_path.unlink(missing_ok=True)
            _build_pdf(images, pdf_path, page_dir, quality)
            if pdf_path.stat().st_size <= pdf_budget:
                archive_path = archive_dir / f"JM{album_id}.zip"
                _write_encrypted_zip(archive_path, password, [(pdf_path, pdf_path.name)])
                shutil.rmtree(page_dir, ignore_errors=True)
                shutil.rmtree(image_dir, ignore_errors=True)
                pdf_path.unlink(missing_ok=True)
                return archive_path, "pdf", quality, ""
        except Exception as exc:
            conversion_error = str(exc).replace("\n", " ")[-300:]
            break

    pdf_path.unlink(missing_ok=True)
    shutil.rmtree(page_dir, ignore_errors=True)
    archive_path = archive_dir / f"JM{album_id}.zip"
    original_files = [
        (source, f"JM{album_id}/{source.relative_to(image_dir).as_posix()}")
        for source in images
    ]
    _write_encrypted_zip(archive_path, password, original_files)
    shutil.rmtree(image_dir, ignore_errors=True)
    return archive_path, "images", None, conversion_error or "PDF 超过上传大小预算"


def download(album_id: str, job_dir: Path, password: str) -> None:
    from jmcomic import JmOption, download_album

    image_dir = job_dir / "images"
    archive_dir = job_dir / "archives"
    image_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    option = build_option(JmOption, image_dir)

    started = time.monotonic()
    result = download_album(album_id, option, check_exception=False)
    download_seconds = time.monotonic() - started
    downloader = result.downloader
    successful_images = sum(
        len(image_list)
        for photo_dict in downloader.download_success_dict.values()
        for image_list in photo_dict.values()
    )
    failed_images = len(downloader.download_failed_image)
    failed_photos = len(downloader.download_failed_photo)
    if successful_images == 0:
        raise RuntimeError("没有成功下载任何图片")
    (job_dir / "progress.json").write_text(
        json.dumps(
            {
                "download_complete": True,
                "downloaded_pages": successful_images,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    started = time.monotonic()
    final_path, output_format, pdf_quality, fallback_reason = create_archive(
        album_id, image_dir, archive_dir, password
    )
    processing_seconds = time.monotonic() - started
    (job_dir / "result.json").write_text(
        json.dumps(
            {
                "successful_images": successful_images,
                "failed_images": failed_images,
                "failed_photos": failed_photos,
                "page_count": successful_images + failed_images,
                "download_seconds": round(download_seconds, 3),
                "processing_seconds": round(processing_seconds, 3),
                "output_format": output_format,
                "pdf_quality": pdf_quality,
                "fallback_reason": fallback_reason,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"JM_ARCHIVE={final_path}")


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--inspect":
        album_id = sys.argv[2].strip()
        if not re.fullmatch(r"\d{1,12}", album_id):
            raise SystemExit("invalid album id")
        inspect_album(album_id)
        return
    if len(sys.argv) != 3:
        raise SystemExit("usage: python -m jm_qqbot.worker ALBUM_ID JOB_DIR | --inspect ALBUM_ID")
    album_id = sys.argv[1].strip()
    if not re.fullmatch(r"\d{1,12}", album_id):
        raise SystemExit("invalid album id")
    password = os.environ.get("JM_ZIP_PASSWORD", "")
    if len(password) < 8:
        raise SystemExit("JM_ZIP_PASSWORD must contain at least 8 characters")
    download(album_id, Path(sys.argv[2]).resolve(), password)


if __name__ == "__main__":
    main()
