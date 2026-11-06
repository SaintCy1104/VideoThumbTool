"""Tải video YouTube và Facebook bằng yt-dlp."""

from __future__ import annotations

import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yt_dlp

ALLOWED_HOSTS = (
    "youtube.com",
    "youtu.be",
    "facebook.com",
    "fb.com",
    "fb.watch",
)


class DownloadError(Exception):
    """Lỗi có thể hiển thị thẳng cho người dùng."""


@dataclass(frozen=True)
class Downloaded:
    title: str
    uploader: str
    path: Path


def _host_allowed(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def _cookies_file(cookie_text: str, directory: Path) -> Path | None:
    """Chuyển cookie dạng ``a=1; b=2`` (copy từ F12) sang định dạng cookies.txt của yt-dlp."""
    text = cookie_text.strip()
    if not text:
        return None
    text = re.sub(r"^cookie:\s*", "", text, flags=re.I)
    lines = ["# Netscape HTTP Cookie File"]
    for part in text.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            lines.append(f".facebook.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}")
            lines.append(f".youtube.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}")
    path = directory / "cookies.txt"
    path.write_text("\n".join(lines) + "\n", "utf-8")
    return path


QUALITY_LIMITS = {"Tối đa": None, "1440p": 1440, "1080p": 1080, "720p": 720, "480p": 480, "360p": 360}


def to_h264(path: Path) -> Path:
    """Chuyển video AV1/VP9 sang H.264 để mở được trên Windows Media Player. Giữ nguyên âm thanh."""
    from thumb import find_ffmpeg

    probe = subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True, errors="replace"
    )
    if re.search(r"Video: h264", probe.stderr):
        return path
    target = path.with_name(path.stem + "_h264.mp4")
    done = subprocess.run(
        [
            find_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "copy", str(target),
        ],
        capture_output=True, text=True, errors="replace",
    )
    if done.returncode != 0 or not target.exists():
        raise DownloadError(f"Không chuyển được sang H.264: {done.stderr.strip()[-200:]}")
    path.unlink(missing_ok=True)
    return target


def download(url: str, output_root: Path, cookie_text: str = "", quality: str = "1080p", h264: bool = False) -> Downloaded:
    """Tải một video vào ``output_root/<nền tảng>/``. Trả về đường dẫn tệp đã tải."""
    url = (url or "").strip()
    if not url:
        raise DownloadError("Hãy dán link video trước.")
    if not _host_allowed(url):
        raise DownloadError("Chỉ hỗ trợ link YouTube và Facebook.")

    output_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    with tempfile.TemporaryDirectory() as temp:
        options = {
            "outtmpl": str(output_root / "%(extractor_key)s" / f"%(title).80B_{stamp}_%(id)s.%(ext)s"),
            # Lấy độ phân giải cao nhất tới 1080p. YouTube thường chỉ có 1080p ở VP9/AV1,
            # nên không ép H.264 trong bộ lọc; H.264 chỉ được ưu tiên khi cùng độ phân giải.
            "format": "bv*+ba/b",
            "format_sort": (
                [f"res:{QUALITY_LIMITS[quality]}", "vcodec:h264", "acodec:m4a", "fps"]
                if QUALITY_LIMITS.get(quality)
                else ["res", "vcodec:h264", "acodec:m4a", "fps"]
            ),
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "restrictfilenames": False,
        }
        cookies = _cookies_file(cookie_text, Path(temp))
        if cookies:
            options["cookiefile"] = str(cookies)
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
        except yt_dlp.utils.YoutubeDLError as exc:
            message = str(exc).replace("ERROR: ", "")[:300]
            if "login" in message.lower() or "cookie" in message.lower() or "private" in message.lower():
                raise DownloadError(
                    "Video này cần đăng nhập hoặc là riêng tư. Dán cookie từ trình duyệt "
                    "(đã đăng nhập tài khoản có quyền xem) vào ô Cookie rồi thử lại."
                ) from exc
            raise DownloadError(message) from exc

    requested = info.get("requested_downloads") or []
    path = Path(requested[0]["filepath"]) if requested else None
    if path is None or not path.exists():
        raise DownloadError("Tải xong nhưng không tìm thấy tệp video.")
    if h264:
        path = to_h264(path)
    return Downloaded(info.get("title") or "", info.get("uploader") or "", path)
