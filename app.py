"""VideoThumbTool: tải video YouTube/Facebook và chỉnh thumbnail."""

from __future__ import annotations

import functools
from pathlib import Path

import gradio as gr

import thumb
import logo_detect
import video_enhance
from downloader import DownloadError, download

ROOT = Path(__file__).resolve().parent
DOWNLOADS = ROOT / "downloads"
OUTPUTS = ROOT / "outputs"
PREVIEW_LIMIT_BYTES = 300 * 1024 * 1024


def _guarded(function):
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except gr.Error:
            raise
        except (DownloadError, thumb.ThumbError) as exc:
            raise gr.Error(str(exc), duration=None) from exc
        except Exception as exc:
            raise gr.Error(f"Lỗi không mong đợi: {exc}", duration=None) from exc

    return wrapper


@_guarded
def handle_download(link: str, cookie: str, quality: str, h264: bool, progress=gr.Progress()):
    progress(0.1, desc="Đang tải video")
    result = download(link, DOWNLOADS, cookie or "", quality, bool(h264))
    info = f"**{result.title or 'Không có tiêu đề'}**"
    if result.uploader:
        info += f" của {result.uploader}"
    info += f"\n\nĐã lưu vào `{result.path}`"
    # Video lớn không đưa vào trình xem trước: Gradio sẽ chuyển đổi cả file (rất lâu với video dài).
    # Tệp vẫn được xử lý bình thường qua đường dẫn đã lưu.
    if result.path.stat().st_size > PREVIEW_LIMIT_BYTES:
        info += "\n\nVideo lớn nên không hiện xem trước. Dùng đường dẫn trên để xử lý trong các tab khác."
        return info, None, str(result.path)
    return info, str(result.path), str(result.path)


@_guarded
def handle_duration(video):
    if not video:
        raise gr.Error("Hãy chọn video trước.")
    seconds = thumb.video_duration(video)
    return gr.update(maximum=max(seconds, 1), value=min(seconds / 3, seconds)), f"Thời lượng: {seconds:.1f} giây"


@_guarded
def handle_frame(video, seconds):
    if not video:
        raise gr.Error("Hãy chọn video trước.")
    frame = thumb.extract_frame(video, float(seconds), OUTPUTS / "khung_hinh")
    return str(frame), str(frame)


@_guarded
def handle_thumbnail(frame, image, size, title, position, color, brightness, zoom, focus_x, focus_y):
    source = image or frame
    if not source:
        raise gr.Error("Hãy lấy khung hình từ video hoặc tải ảnh lên.")
    output = thumb.make_thumbnail(
        source, size, title, position, color, float(brightness), float(zoom),
        float(focus_x), float(focus_y), OUTPUTS,
    )
    return str(output)


@_guarded
def handle_enhance(video, use_logo, x, y, w, h, upscale, denoise, sharpen, strong, progress=gr.Progress()):
    if not video:
        raise gr.Error("Hãy chọn video trước.")
    region = (float(x), float(y), float(w), float(h)) if use_logo else None
    progress(0.1, desc="Đang xử lý video (có thể lâu với video dài)")
    output = video_enhance.enhance_video(video, region, upscale, denoise, sharpen, OUTPUTS, strong)
    return str(output), str(output)


def _draw_region(frame_path, x, y, w, h):
    """Ảnh khung hình có khung đỏ đánh dấu vùng logo, tính theo phần trăm."""
    from PIL import Image, ImageDraw

    with Image.open(frame_path) as source:
        image = source.convert("RGB")
    width, height = image.size
    px, py, pw, ph = video_enhance.region_pixels(width, height, float(x), float(y), float(w), float(h))
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rectangle(
        (px, py, px + pw, py + ph),
        outline=(255, 45, 45, 255),
        width=max(2, width // 200),
        fill=(255, 45, 45, 70),
    )
    return image


@_guarded
def handle_enh_frame(video, seconds):
    if not video:
        raise gr.Error("Hãy tải video lên trước.")
    frame = thumb.extract_frame(video, float(seconds or 0), OUTPUTS / "khung_logo")
    return str(frame), str(frame), []


@_guarded
def handle_enh_detect(video, seconds):
    if not video:
        raise gr.Error("Hãy tải video lên trước.")
    frame = thumb.extract_frame(video, float(seconds or 0), OUTPUTS / "khung_logo")
    detection = logo_detect.detect_logo(video)
    if detection is None:
        return str(frame), str(frame), [], 0, 0, 0, 0, (
            "Không tìm thấy logo nào đứng yên rõ ràng. Hãy chọn vùng bằng cách bấm 2 lần lên khung hình."
        )
    overlay = _draw_region(str(frame), detection.x, detection.y, detection.w, detection.h)
    message = (
        f"Đã phát hiện logo (độ tin cậy {detection.confidence}). Kiểm tra khung đỏ; "
        "nếu chưa khít, tinh chỉnh bằng thanh trượt hoặc bấm 2 lần lại."
    )
    return overlay, str(frame), [], detection.x, detection.y, detection.w, detection.h, message


@_guarded
def handle_enh_redraw(frame, x, y, w, h):
    if not frame:
        return None
    return _draw_region(frame, x, y, w, h)


@_guarded
def handle_enh_pick(frame, points, x, y, w, h, evt: gr.SelectData):
    if not frame:
        raise gr.Error("Hãy lấy khung hình trước.")
    from PIL import Image

    with Image.open(frame) as source:
        width, height = source.size
    points = list(points or []) + [tuple(evt.index)]
    if len(points) < 2:
        # Chưa đủ hai điểm: chỉ hiện điểm đầu tiên để người dùng biết đã bấm.
        return frame, points, x, y, w, h
    (x1, y1), (x2, y2) = points[0], points[1]
    left, right = sorted((x1, x2))
    top, bottom = sorted((y1, y2))
    new_x = left / width * 100
    new_y = top / height * 100
    new_w = (right - left) / width * 100
    new_h = (bottom - top) / height * 100
    return _draw_region(frame, new_x, new_y, new_w, new_h), [], new_x, new_y, new_w, new_h


@_guarded
def handle_add_logo(video, logo, position, scale, opacity, seconds, progress=gr.Progress()):
    if not video:
        raise gr.Error("Hãy chọn video trước.")
    if not logo:
        raise gr.Error("Hãy tải ảnh logo (nên dùng PNG nền trong suốt).")
    progress(0.1, desc="Đang chèn logo")
    output = video_enhance.add_logo(video, logo, position, float(scale), float(opacity), OUTPUTS, float(seconds or 0))
    return str(output), str(output)


with gr.Blocks(title="VideoThumbTool") as demo:
    gr.Markdown("# VideoThumbTool")

    with gr.Tab("1. Tải video"):
        link = gr.Textbox(label="Link YouTube hoặc Facebook", placeholder="Dán link video")
        with gr.Accordion("Cookie (chỉ cần cho video riêng tư hoặc cần đăng nhập)", open=False):
            gr.Markdown(
                "Mở trang video trong trình duyệt đã đăng nhập, nhấn F12, tab Network, chọn một "
                "yêu cầu tới trang đó, sao chép giá trị `Cookie` và dán vào ô dưới. Cookie đăng nhập "
                "có giá trị như mật khẩu, tool chỉ dùng trong lúc tải và không lưu lại."
            )
            cookie = gr.Textbox(label="Cookie", type="password")
        quality = gr.Radio(["Tối đa", "1440p", "1080p", "720p", "480p", "360p"], value="1080p", label="Độ phân giải (Tối đa có thể là 4K)")
        h264 = gr.Checkbox(value=False, label="Chuyển sang H.264 để mở được trên Windows Media Player (chậm với video dài)")
        download_button = gr.Button("Tải về", variant="primary")
        download_status = gr.Markdown()
        video_output = gr.Video(label="Video đã tải", interactive=False)
        downloaded_path = gr.State("")

    with gr.Tab("2. Chỉnh thumbnail"):
        with gr.Row():
            video_input = gr.Video(label="Video nguồn (để lấy khung hình)", sources=["upload"])
            image_input = gr.Image(label="Hoặc ảnh có sẵn", type="filepath", sources=["upload"])
        with gr.Row():
            seconds = gr.Slider(0, 1, value=0, step=0.5, label="Mốc thời gian lấy khung hình (giây)")
            duration_button = gr.Button("Đọc thời lượng video")
        duration_info = gr.Markdown()
        frame_button = gr.Button("Lấy khung hình tại mốc này")
        source_state = gr.State("")
        frame_preview = gr.Image(label="Khung hình đã chọn", interactive=False)

        gr.Markdown("### Chỉnh sửa")
        with gr.Row():
            size = gr.Radio(list(thumb.SIZES), value="Ngang 1280x720 (YouTube)", label="Khổ ảnh")
            brightness = gr.Slider(0.5, 1.5, value=1.0, step=0.05, label="Độ sáng")
            zoom = gr.Slider(1.0, 2.0, value=1.0, step=0.05, label="Phóng to")
        with gr.Row():
            focus_x = gr.Slider(0, 1, value=0.5, step=0.05, label="Vị trí ngang khi cắt")
            focus_y = gr.Slider(0, 1, value=0.5, step=0.05, label="Vị trí dọc khi cắt")
        title = gr.Textbox(label="Tiêu đề trên ảnh (để trống nếu không cần)", lines=2)
        with gr.Row():
            position = gr.Radio(["Trên", "Giữa", "Dưới"], value="Dưới", label="Vị trí chữ")
            color = gr.Radio(list(thumb.COLORS), value="Vàng", label="Màu chữ")
        make_button = gr.Button("Tạo thumbnail", variant="primary")
        result_image = gr.Image(label="Thumbnail", type="filepath", interactive=False)

    with gr.Tab("3. Xóa logo & nâng chất lượng"):
        gr.Markdown(
            "Chỉ dùng cho video **của bạn** hoặc video bạn có quyền chỉnh sửa. "
            "Lấy khung hình, rồi **bấm 2 lần lên logo** (góc trên trái, góc dưới phải). "
            "Khung đỏ hiện ngay và có thể tinh chỉnh bằng thanh trượt. Không cần tải lại video."
        )
        enh_seconds = gr.Number(value=0, label="Lấy khung hình tại giây thứ", precision=1)
        enh_frame_button = gr.Button("Lấy khung hình để chọn logo")
        enh_detect_button = gr.Button("Tự phát hiện logo")
        enh_message = gr.Markdown()
        enh_frame_path = gr.State("")
        enh_points = gr.State([])
        enh_image = gr.Image(label="Bấm 2 lần lên logo", type="filepath", interactive=True)
        with gr.Row():
            logo_x = gr.Slider(0, 100, value=0, step=0.1, label="X (%)")
            logo_y = gr.Slider(0, 100, value=0, step=0.1, label="Y (%)")
            logo_w = gr.Slider(0, 100, value=0, step=0.1, label="W (%)")
            logo_h = gr.Slider(0, 100, value=0, step=0.1, label="H (%)")
        use_logo = gr.Checkbox(value=True, label="Xóa logo ở vùng đã chọn")
        with gr.Row():
            do_upscale = gr.Checkbox(value=False, label="Phóng to 2 lần (chậm, file lớn)")
            do_denoise = gr.Checkbox(value=True, label="Khử nhiễu")
            do_sharpen = gr.Checkbox(value=True, label="Làm nét")
            do_strong = gr.Checkbox(value=False, label="Làm nét mạnh + chất lượng cao (chậm hơn, file lớn hơn)")
        enh_video_input = gr.Video(label="Video đang xử lý", sources=["upload"])
        enhance_button = gr.Button("Xử lý video", variant="primary")
        enhanced_video = gr.Video(label="Kết quả", interactive=False)
        enhanced_file = gr.File(label="Tệp kết quả")

        enh_frame_button.click(
            handle_enh_frame,
            [enh_video_input, enh_seconds],
            [enh_image, enh_frame_path, enh_points],
        )
        enh_detect_button.click(
            handle_enh_detect,
            [enh_video_input, enh_seconds],
            [enh_image, enh_frame_path, enh_points, logo_x, logo_y, logo_w, logo_h, enh_message],
        )
        enh_image.select(
            handle_enh_pick,
            [enh_frame_path, enh_points, logo_x, logo_y, logo_w, logo_h],
            [enh_image, enh_points, logo_x, logo_y, logo_w, logo_h],
        )
        for slider in (logo_x, logo_y, logo_w, logo_h):
            slider.release(
                handle_enh_redraw,
                [enh_frame_path, logo_x, logo_y, logo_w, logo_h],
                [enh_image],
            )
        enhance_button.click(
            handle_enhance,
            [enh_video_input, use_logo, logo_x, logo_y, logo_w, logo_h, do_upscale, do_denoise, do_sharpen, do_strong],
            [enhanced_video, enhanced_file],
        )

    with gr.Tab("4. Thêm logo"):
        gr.Markdown("Chèn logo của bạn (PNG nền trong suốt) vào video của bạn.")
        logo_video = gr.Video(label="Video", sources=["upload"])
        logo_image = gr.Image(label="Logo (PNG nền trong suốt)", type="filepath", sources=["upload"])
        with gr.Row():
            logo_position = gr.Radio(list(video_enhance.POSITIONS), value="Dưới phải", label="Vị trí")
            logo_scale = gr.Slider(1, 60, value=18, step=1, label="Kích thước (% chiều rộng video)")
            logo_opacity = gr.Slider(0.1, 1.0, value=0.85, step=0.05, label="Độ trong suốt")
        logo_seconds = gr.Number(value=3600, label="Chỉ chèn trong N giây đầu (0 = suốt video, 3600 = 1 tiếng)", precision=0)
        add_logo_button = gr.Button("Thêm logo", variant="primary")
        added_video = gr.Video(label="Kết quả", interactive=False)
        added_file = gr.File(label="Tệp kết quả")
        add_logo_button.click(
            handle_add_logo,
            [logo_video, logo_image, logo_position, logo_scale, logo_opacity, logo_seconds],
            [added_video, added_file],
        )

    download_button.click(
        handle_download,
        [link, cookie, quality, h264],
        [download_status, video_output, downloaded_path],
    ).then(
        lambda path: (path, path, path),
        [downloaded_path],
        [video_input, enh_video_input, logo_video],
    )
    duration_button.click(handle_duration, [video_input], [seconds, duration_info])
    frame_button.click(handle_frame, [video_input, seconds], [frame_preview, source_state])
    make_button.click(
        handle_thumbnail,
        [source_state, image_input, size, title, position, color, brightness, zoom, focus_x, focus_y],
        [result_image],
    )


if __name__ == "__main__":
    DOWNLOADS.mkdir(exist_ok=True)
    OUTPUTS.mkdir(exist_ok=True)
    demo.queue(default_concurrency_limit=3).launch(
        server_name="127.0.0.1",
        inbrowser=True,
        allowed_paths=[str(DOWNLOADS), str(OUTPUTS)],
    )
