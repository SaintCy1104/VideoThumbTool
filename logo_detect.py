"""Tự phát hiện logo: tìm vùng đứng yên qua nhiều khung hình và có cạnh rõ.

Logo hoặc watermark thường nằm cố định ở một chỗ trong suốt video, trong khi nội dung chuyển
động. Hàm này lấy mẫu vài khung hình, tìm các pixel gần như không đổi nhưng có cạnh, rồi chọn
vùng nhỏ giống logo nhất (ưu tiên góc). Kết quả là một khung đề xuất để người dùng xác nhận.
Đây là phương pháp heuristic: video có nền tĩnh lớn hoặc khung đen có thể gây nhầm lẫn.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from thumb import ThumbError, find_ffmpeg, video_duration
from video_enhance import probe_size

ANALYSIS_WIDTH = 320
MAX_SAMPLES = 40
STATIC_STD = 6.0      # độ lệch chuẩn theo thời gian tối đa của pixel "đứng yên"
EDGE_MIN = 35.0       # độ mạnh cạnh tối thiểu của pixel "có hình dạng"
MIN_AREA = 0.0005     # tỷ lệ diện tích tối thiểu so với khung hình
MAX_AREA = 0.15       # tỷ lệ diện tích tối đa so với khung hình


@dataclass(frozen=True)
class Detection:
    x: float
    y: float
    w: float
    h: float
    confidence: str


def _sample_frames(video: str) -> np.ndarray:
    """Lấy các khung hình rải đều bằng cách tua tới từng mốc, không giải mã cả video."""
    original_w, original_h = probe_size(video)
    height = round(original_h * ANALYSIS_WIDTH / original_w)
    height -= height % 2
    duration = video_duration(video)
    if duration <= 0:
        raise ThumbError("Không đọc được thời lượng video.")
    count = min(MAX_SAMPLES, max(16, int(duration // 30)))
    frame_bytes = ANALYSIS_WIDTH * height
    frames = []
    for index in range(count):
        moment = duration * (index + 0.5) / count
        done = subprocess.run(
            [
                find_ffmpeg(), "-v", "error", "-ss", f"{moment:.2f}", "-i", video,
                "-frames:v", "1",
                "-vf", f"scale={ANALYSIS_WIDTH}:{height},format=gray",
                "-f", "rawvideo", "-",
            ],
            capture_output=True,
        )
        if len(done.stdout) == frame_bytes:
            frames.append(np.frombuffer(done.stdout, np.uint8).reshape(height, ANALYSIS_WIDTH))
    if len(frames) < 4:
        raise ThumbError("Không lấy đủ khung hình để tự phát hiện logo.")
    return np.stack(frames).astype(np.float32)


def detect_logo(video: str) -> Detection | None:
    """Trả về vùng logo đề xuất (tính bằng phần trăm kích thước video), hoặc None nếu không thấy."""
    frames = _sample_frames(video)
    height, width = frames.shape[1], frames.shape[2]

    median = np.median(frames, axis=0)
    temporal_std = frames.std(axis=0)
    grad_y, grad_x = np.gradient(median)
    edge = np.hypot(grad_x, grad_y)

    candidate = (temporal_std < STATIC_STD) & (edge > EDGE_MIN)
    if not candidate.any():
        return None
    # Nối các nét chữ và hình gần nhau thành một khối.
    candidate = ndimage.binary_dilation(candidate, iterations=3)
    labels, count = ndimage.label(candidate)

    best: tuple[float, tuple[int, int, int, int]] | None = None
    frame_area = height * width
    for index, slice_pair in enumerate(ndimage.find_objects(labels), start=1):
        if slice_pair is None:
            continue
        ys, xs = slice_pair
        box_h = ys.stop - ys.start
        box_w = xs.stop - xs.start
        area_ratio = (box_h * box_w) / frame_area
        if not (MIN_AREA <= area_ratio <= MAX_AREA):
            continue
        # Đường kẻ dài ngang hoặc dọc (viền khung đen, thanh đen) không phải logo.
        if box_w > 0.5 * width or box_h > 0.5 * height:
            continue
        mask = labels[ys, xs] == index
        strength = float(edge[ys, xs][mask].mean())
        # Ưu tiên vùng gần góc, vì logo thường nằm ở đó.
        center_x = (xs.start + xs.stop) / 2 / width
        center_y = (ys.start + ys.stop) / 2 / height
        near_corner = (center_x < 0.25 or center_x > 0.75) and (center_y < 0.25 or center_y > 0.75)
        score = strength * (1.5 if near_corner else 1.0)
        if best is None or score > best[0]:
            best = (score, (xs.start, ys.start, xs.stop, ys.stop))

    if best is None:
        return None
    left, top, right, bottom = best[1]
    # Nới rộng khung một chút để che hết viền mờ của logo.
    pad_x = max(2, int((right - left) * 0.1))
    pad_y = max(2, int((bottom - top) * 0.1))
    left = max(0, left - pad_x)
    top = max(0, top - pad_y)
    right = min(width, right + pad_x)
    bottom = min(height, bottom + pad_y)
    confidence = "cao" if best[0] > EDGE_MIN * 1.5 else "trung bình"
    return Detection(
        x=left / width * 100,
        y=top / height * 100,
        w=(right - left) / width * 100,
        h=(bottom - top) / height * 100,
        confidence=confidence,
    )
