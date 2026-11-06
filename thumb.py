"""Tạo và chỉnh thumbnail: lấy khung hình từ video, cắt đúng khổ, vẽ chữ, chỉnh màu."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

SIZES = {
    "Ngang 1280x720 (YouTube)": (1280, 720),
    "Vuông 1080x1080": (1080, 1080),
    "Dọc 1080x1920 (Reels, Shorts)": (1080, 1920),
}
COLORS = {
    "Vàng": "#FFD400",
    "Trắng": "#FFFFFF",
    "Đỏ": "#FF2D2D",
    "Xanh lá": "#3DFF6E",
    "Xanh dương": "#3FA9FF",
}
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
]


class ThumbError(Exception):
    """Lỗi có thể hiển thị thẳng cho người dùng."""


def find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    matches = glob.glob(
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\bin\ffmpeg.exe"),
        recursive=True,
    )
    if matches:
        return matches[0]
    raise ThumbError("Không tìm thấy ffmpeg. Hãy cài bằng lệnh: winget install Gyan.FFmpeg")


def video_duration(video: str) -> float:
    """Thời lượng video tính bằng giây, đọc từ ffmpeg."""
    done = subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-i", video], capture_output=True, text=True, errors="replace"
    )
    for line in done.stderr.splitlines():
        if "Duration:" in line:
            clock = line.split("Duration:")[1].split(",")[0].strip()
            hours, minutes, seconds = clock.split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    raise ThumbError("Không đọc được thời lượng video.")


def extract_frame(video: str, seconds: float, output_dir: Path) -> Path:
    """Lấy một khung hình tại mốc thời gian ``seconds``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"khung_{time.strftime('%Y%m%d_%H%M%S')}_{int(seconds * 100):06d}.jpg"
    done = subprocess.run(
        [
            find_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{seconds:.2f}", "-i", video,
            "-frames:v", "1", "-q:v", "2", str(target),
        ],
        capture_output=True, text=True, errors="replace",
    )
    if done.returncode != 0 or not target.exists():
        raise ThumbError(f"Không lấy được khung hình: {done.stderr.strip()[-200:]}")
    return target


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines():
        current = ""
        for word in paragraph.split():
            attempt = f"{current} {word}".strip()
            if current and draw.textlength(attempt, font=font) > width:
                lines.append(current)
                current = word
            else:
                current = attempt
        if current:
            lines.append(current)
    return lines


def make_thumbnail(
    source: str,
    size_name: str,
    title: str,
    position: str,
    color: str,
    brightness: float,
    zoom: float,
    focus_x: float,
    focus_y: float,
    output_dir: Path,
) -> Path:
    """Cắt ảnh theo khổ đã chọn, chỉnh sáng, rồi vẽ tiêu đề có viền.

    ``zoom`` (1.0 = vừa khung) và ``focus_x``, ``focus_y`` (0 đến 1) cho phép chọn phần ảnh
    giữ lại khi cắt.
    """
    if size_name not in SIZES:
        raise ThumbError("Khổ ảnh không hợp lệ.")
    width, height = SIZES[size_name]
    with Image.open(source) as original:
        image = original.convert("RGB")

    # Phóng to và cắt quanh điểm người dùng chọn.
    base_ratio = max(width / image.width, height / image.height)
    scale = base_ratio * max(zoom, 1.0)
    resized = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    left = int((resized.width - width) * min(max(focus_x, 0), 1))
    top = int((resized.height - height) * min(max(focus_y, 0), 1))
    canvas = resized.crop((left, top, left + width, top + height))

    if brightness != 1.0:
        canvas = ImageEnhance.Brightness(canvas).enhance(brightness)

    text = (title or "").strip().upper()
    if text:
        draw = ImageDraw.Draw(canvas)
        margin = width // 20
        font_size = max(width // 11, 24)
        while True:
            font = _font(font_size)
            lines = _wrap(draw, text, font, width - margin * 2)
            line_height = int(font_size * 1.2)
            block = line_height * len(lines)
            if block <= height * 0.45 or font_size <= 24:
                break
            font_size -= 4
        y = {
            "Trên": margin,
            "Giữa": (height - block) // 2,
            "Dưới": height - block - margin,
        }[position]
        for line in lines:
            x = (width - draw.textlength(line, font=font)) / 2
            draw.text(
                (x, y),
                line,
                font=font,
                fill=COLORS.get(color, "#FFFFFF"),
                stroke_width=max(3, font_size // 12),
                stroke_fill="black",
            )
            y += line_height

    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"thumbnail_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns() // 1_000_000 % 1000:03d}.jpg"
    canvas.save(output, quality=92)
    return output
