"""Xóa logo (vùng chọn trước) và nâng chất lượng hình ảnh bằng ffmpeg.

Công cụ không tự nhận diện logo. Người dùng chỉ định vùng bằng tỷ lệ phần trăm,
nên chỉ nên dùng cho video của chính bạn hoặc video bạn có quyền chỉnh sửa.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from thumb import ThumbError, find_ffmpeg


_ENCODER: list[str] | None = None


def _encoder_args(high_quality: bool = False) -> list[str]:
    """Bộ mã hóa nhanh nhất máy hỗ trợ: NVENC nếu driver đủ mới, không thì libx264 preset nhanh."""
    global _ENCODER
    if _ENCODER is None:
        probe = subprocess.run(
            [
                find_ffmpeg(), "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=640x480:d=0.2",
                "-c:v", "h264_nvenc", "-f", "null", "-",
            ],
            capture_output=True,
        )
        if probe.returncode == 0:
            _ENCODER = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20"]
        else:
            _ENCODER = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    if high_quality:
        # Chất lượng cao: bitrate lớn hơn, nét hơn, file lớn hơn.
        return ["-c:v", "libx264", "-preset", "medium", "-crf", "16"]
    return _ENCODER


def probe_size(video: str) -> tuple[int, int]:
    done = subprocess.run(
        [
            str(Path(find_ffmpeg()).with_name("ffprobe.exe")),
            "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "json", video,
        ],
        capture_output=True, text=True, errors="replace",
    )
    try:
        stream = json.loads(done.stdout)["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except (ValueError, KeyError, IndexError) as exc:
        raise ThumbError("Không đọc được kích thước video.") from exc


def region_pixels(width: int, height: int, x: float, y: float, w: float, h: float) -> tuple[int, int, int, int]:
    """Đổi vùng từ phần trăm sang pixel, căn về số chẵn để ffmpeg không báo lỗi."""
    px = max(0, int(width * x / 100))
    py = max(0, int(height * y / 100))
    pw = max(2, int(width * w / 100))
    ph = max(2, int(height * h / 100))
    pw -= pw % 2
    ph -= ph % 2
    px = min(px, max(0, width - pw))
    py = min(py, max(0, height - ph))
    return px, py, pw, ph


def enhance_video(
    video: str,
    logo_region: tuple[float, float, float, float] | None,
    upscale: bool,
    denoise: bool,
    sharpen: bool,
    output_dir: Path,
    strong_sharpen: bool = False,
) -> Path:
    """Xóa logo ở vùng đã chọn, khử nhiễu, làm nét và (tùy chọn) phóng to 2 lần."""
    width, height = probe_size(video)
    filters: list[str] = []
    if logo_region is not None and any(logo_region):
        x, y, w, h = region_pixels(width, height, *logo_region)
        filters.append(f"delogo=x={x}:y={y}:w={w}:h={h}")
    if denoise:
        filters.append("hqdn3d=2:1.5:3:2.5")
    if upscale:
        filters.append("scale=trunc(iw*2/2)*2:trunc(ih*2/2)*2:flags=lanczos")
    if sharpen:
        filters.append("unsharp=5:5:1.6:5:5:0.0" if strong_sharpen else "unsharp=5:5:0.9:5:5:0.0")
    if not filters:
        raise ThumbError("Chưa chọn thao tác nào.")

    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"nang_cap_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
    done = subprocess.run(
        [
            find_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-i", video,
            "-vf", ",".join(filters),
            *_encoder_args(strong_sharpen),
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            str(output),
        ],
        capture_output=True, text=True, errors="replace",
    )
    if done.returncode != 0 or not output.exists():
        raise ThumbError(f"Xử lý video thất bại: {done.stderr.strip()[-300:]}")
    return output


POSITIONS = {
    "Trên trái": ("W*0.02", "H*0.02"),
    "Trên phải": ("W-w-W*0.02", "H*0.02"),
    "Dưới trái": ("W*0.02", "H-h-H*0.02"),
    "Dưới phải": ("W-w-W*0.02", "H-h-H*0.02"),
    "Giữa": ("(W-w)/2", "(H-h)/2"),
}


def add_logo(
    video: str,
    logo: str,
    position: str,
    scale_percent: float,
    opacity: float,
    output_dir: Path,
    seconds: float = 0,
) -> Path:
    """Chèn ảnh logo (PNG trong suốt được) vào video ở vị trí và kích thước đã chọn."""
    if position not in POSITIONS:
        raise ThumbError("Vị trí logo không hợp lệ.")
    x, y = POSITIONS[position]
    scale = min(max(scale_percent, 1), 60) / 100
    alpha = min(max(opacity, 0.1), 1.0)
    # seconds = 0 nghĩa là chèn suốt video; > 0 thì chỉ chèn trong N giây đầu.
    enable = f":enable='lte(t,{seconds:.1f})'" if seconds and seconds > 0 else ""
    graph = (
        f"[1:v][0:v]scale2ref=w=main_w*{scale:.3f}:h=ow/mdar[logo][base];"
        f"[logo]format=rgba,colorchannelmixer=aa={alpha:.2f}[logoa];"
        f"[base][logoa]overlay=x={x}:y={y}{enable}[out]"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"co_logo_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
    done = subprocess.run(
        [
            find_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
            "-i", video, "-i", logo,
            "-filter_complex", graph,
            "-map", "[out]", "-map", "0:a?",
            *_encoder_args(), "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            str(output),
        ],
        capture_output=True, text=True, errors="replace",
    )
    if done.returncode != 0 or not output.exists():
        raise ThumbError(f"Thêm logo thất bại: {done.stderr.strip()[-300:]}")
    return output
