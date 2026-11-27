"""语音文件层：目录、ffmpeg 转码、时长探测、HTTP Range 响应。

为什么需要 ffmpeg：
    浏览器 MediaRecorder 在 Chrome 出 webm/opus、iOS Safari 出 mp4/aac，
    两端互不认。统一转 one 份 mp3 是最省心的做法；没有 ffmpeg 时退化为「原样存」，
    播放兼容性由前端按 MIME 兜底，功能不阻塞。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Iterator

from fastapi import Request
from fastapi.responses import StreamingResponse

from .config import settings

# 允许上传的容器/编码：只收录 MediaRecorder 与移动端常见的几种
ALLOWED_MIME = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}

_CHUNK = 64 * 1024


def base_dir() -> str:
    if settings.voice_dir:
        return os.path.abspath(settings.voice_dir)
    sqlite_path = settings.sqlite_path
    if not sqlite_path or sqlite_path.startswith(":memory:"):
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    else:
        root = os.path.dirname(os.path.abspath(sqlite_path))
    return os.path.join(root, "voice")


def ensure_dir() -> str:
    path = base_dir()
    os.makedirs(path, exist_ok=True)
    return path


def resolve(rel_path: str | None) -> str | None:
    """把库里存的相对路径还原成绝对路径，并挡住目录穿越。"""
    if not rel_path:
        return None
    root = base_dir()
    target = os.path.abspath(os.path.join(root, rel_path))
    if not target.startswith(root + os.sep) or not os.path.isfile(target):
        return None
    return target


def find_ffmpeg() -> str | None:
    if settings.ffmpeg_path and os.path.isfile(settings.ffmpeg_path):
        return settings.ffmpeg_path
    found = shutil.which("ffmpeg")
    if found:
        return found
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for cand in (
        os.path.join(here, "tools", "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(here, "tools", "ffmpeg", "ffmpeg.exe"),
    ):
        if os.path.isfile(cand):
            return cand
    return None


def _ffprobe(ffmpeg: str) -> str | None:
    stem, ext = os.path.splitext(ffmpeg)
    cand = stem.replace("ffmpeg", "ffprobe") + ext if "ffmpeg" in stem else None
    if cand and os.path.isfile(cand):
        return cand
    return shutil.which("ffprobe")


def probe_duration_ms(path: str) -> int:
    """秒 -> 毫秒。优先 ffprobe，其次 wav 头解析，最后 0（前端按字数估算）。"""
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        probe = _ffprobe(ffmpeg)
        if probe:
            try:
                out = subprocess.run(
                    [probe, "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=nw=1:nk=1", path],
                    capture_output=True, text=True, timeout=10,
                )
                if out.returncode == 0:
                    return max(0, int(float(out.stdout.strip()) * 1000))
            except Exception:
                pass
    try:
        return _wav_duration_ms(path)
    except Exception:
        return 0


def _wav_duration_ms(path: str) -> int:
    import struct

    with open(path, "rb") as fh:
        head = fh.read(44)
    if len(head) < 44 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        return 0
    byte_rate = struct.unpack("<I", head[28:32])[0]
    data_size = struct.unpack("<I", head[40:44])[0]
    if byte_rate <= 0:
        return 0
    return int(data_size / byte_rate * 1000)


def transcode_to_mp3(src: str, dst: str) -> bool:
    """voice 场景 64 kbps 单声道足够，体积约 8 KB/秒。失败返回 False 由调用方降级。"""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False
    if os.path.abspath(src) == os.path.abspath(dst):
        return False
    try:
        proc = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", src,
             "-vn", "-ac", "1", "-ar", "24000", "-b:a", "64k", dst],
            capture_output=True, timeout=120,
        )
        return proc.returncode == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 0
    except Exception:
        return False


def _file_iter(path: str, start: int, end: int) -> Iterator[bytes]:
    with open(path, "rb") as fh:
        fh.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = fh.read(min(_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def audio_response(path: str, request: Request, mime: str) -> StreamingResponse:
    """支持 Range：iOS Safari 不给 206 就不肯播，拖动进度条也需要它。"""
    size = os.path.getsize(path)
    if size <= 0:
        raise FileNotFoundError(path)
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=86400",
    }
    raw = (request.headers.get("range") or "").strip()
    match = _RANGE_RE.match(raw) if raw else None
    if match:
        start_s, end_s = match.group(1), match.group(2)
        if start_s == "" and end_s == "":
            start, end = 0, size - 1
        elif start_s == "":
            length = min(int(end_s), size)
            start, end = max(0, size - length), size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            return StreamingResponse(
                iter(()), status_code=416,
                headers={**headers, "Content-Range": f"bytes */{size}"},
            )
        return StreamingResponse(
            _file_iter(path, start, end),
            status_code=206,
            media_type=mime,
            headers={
                **headers,
                "Content-Range": f"bytes {start}-{end}/{size}",
                "Content-Length": str(end - start + 1),
            },
        )
    return StreamingResponse(
        _file_iter(path, 0, size - 1),
        media_type=mime,
        headers={**headers, "Content-Length": str(size)},
    )
