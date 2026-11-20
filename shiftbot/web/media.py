"""Normalize photos and verify the duration and format of article videos."""
from __future__ import annotations

import io
import json
import subprocess
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PIL import Image, ImageOps

from .settings import InputError, text

IMAGE_LIMIT = 5 * 1024 * 1024
VIDEO_LIMIT = 20 * 1024 * 1024
VIDEO_SECONDS = 20
ARTICLE_REQUEST_LIMIT = 32 * 1024 * 1024
PHOTO_REQUEST_LIMIT = IMAGE_LIMIT + 128 * 1024
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_FORMATS = {
    ".mp4": ("mov", "video/mp4", {"h264", "av1"}),
    ".mov": ("mov", "video/mp4", {"h264", "av1"}),
    ".webm": ("matroska", "video/webm", {"vp8", "vp9", "av1"}),
}


@dataclass(frozen=True)
class Media:
    name: str
    mime: str
    content: bytes
    seconds: float | None = None


def file_contents(upload, limit, label):
    raw = upload.stream.read(limit + 1)
    if not raw or len(raw) > limit:
        raise InputError(f"Выберите непустой файл. {label} до {limit // (1024 * 1024)} МБ.")
    return raw


def photo_content(upload):
    raw = file_contents(upload, IMAGE_LIMIT, "Фото")
    try:
        with Image.open(io.BytesIO(raw), formats=("JPEG", "PNG", "WEBP")) as original:
            if original.width * original.height > 24_000_000:
                raise InputError("Фото слишком большое. Допускается до 24 мегапикселей.")
            image = ImageOps.exif_transpose(original)
            mode = "RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB"
            image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
            # Convert image pixels and omit metadata such as location and camera information.
            output = io.BytesIO()
            image.convert(mode).save(output, format="WEBP", quality=88, method=4)
    except (Image.DecompressionBombError, OSError, SyntaxError):
        raise InputError("Загрузите корректное фото в формате JPG, PNG или WebP.") from None
    return output.getvalue()


def duration(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() and result > 0 else None


def video_content(upload, name, format_info):
    demuxer, mime, codecs = format_info
    raw = file_contents(upload, VIDEO_LIMIT, "Видео")
    if (demuxer == "mov" and raw[4:8] != b"ftyp") or (
        demuxer == "matroska" and (raw[:4] != b"\x1a\x45\xdf\xa3" or b"\x42\x82\x84webm" not in raw[:4096])
    ):
        raise InputError("Загрузите корректный файл MP4, MOV или WebM.")
    with tempfile.TemporaryDirectory(prefix="uppetit-video-") as directory:
        source = Path(directory) / "clip"
        source.write_bytes(raw)
        try:
            result = subprocess.run([
                "ffprobe", "-v", "error", "-max_alloc", "67108864", "-protocol_whitelist", "file",
                "-f", demuxer, "-show_entries",
                "format=duration:stream=codec_type,codec_name,duration,width,height:stream_disposition=attached_pic",
                "-of", "json", "-i", str(source),
            ], capture_output=True, timeout=10)
        except FileNotFoundError:
            raise InputError("Для загрузки видео нужен ffprobe. Установите пакет FFmpeg на машине панели.") from None
        except subprocess.TimeoutExpired:
            raise InputError("Видео не удалось прочитать за 10 секунд. Сохраните его в MP4 или WebM.") from None
    if result.returncode or len(result.stdout) > 65_536:
        raise InputError("Видео повреждено или его формат не поддерживается.")
    try:
        info = json.loads(result.stdout)
    except (UnicodeError, ValueError):
        raise InputError("Не удалось прочитать параметры видео.") from None
    streams = info.get("streams", [])
    video = [s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
    if len(video) != 1 or video[0].get("codec_name") not in codecs or not video[0].get("width") or not video[0].get("height"):
        raise InputError("Используйте MP4 или MOV с H.264 либо WebM с VP8 или VP9.")
    length = max(filter(None, (duration(s.get("duration")) for s in [info.get("format", {}), *streams])),
                 default=None)
    if length is None:
        raise InputError("Не удалось определить длительность видео. Сохраните его в MP4 или WebM.")
    if length > VIDEO_SECONDS:
        raise InputError("Видео должно длиться не более 20 секунд.")
    return Media(name, mime, raw, float(length))


def article_media(upload):
    basename = upload.filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = text({"name": basename}, "name", "Имя файла", limit=180)
    ext = Path(name).suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return Media(name, "image/webp", photo_content(upload))
    if ext in VIDEO_FORMATS:
        return video_content(upload, name, VIDEO_FORMATS[ext])
    raise InputError("Выберите фото JPG, PNG, WebP или видео MP4, MOV, WebM.")
