import json
import mimetypes
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".heif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


def media_type_for(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "photo"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def analyze_image(path: Path, derivative_root: Path, asset_id: str) -> tuple[dict[str, Any], Path]:
    derivative_root.mkdir(parents=True, exist_ok=True)
    thumbnail = derivative_root / f"{asset_id}.jpg"
    with Image.open(path) as image:
        image.load()
        width, height = image.size
        rgb = image.convert("RGB")
        rgb.thumbnail((1600, 1600))
        rgb.save(thumbnail, "JPEG", quality=88, optimize=True)
        stat = ImageStat.Stat(image.convert("RGB"))
        brightness = sum(stat.mean) / 3 / 255
        sharpness_proxy = sum(stat.var) / 3
        info = dict(image.info)
        data = {
            "width": width,
            "height": height,
            "megapixels": round(width * height / 1_000_000, 3),
            "aspect_ratio": round(width / height, 4) if height else None,
            "format": image.format,
            "mode": image.mode,
            "exif_keys": sorted(str(key) for key in image.getexif().keys()),
            "brightness": round(brightness, 4),
            "sharpness_proxy": round(sharpness_proxy, 2),
            "thumbnail": str(thumbnail),
            "metadata_keys": sorted(str(key) for key in info.keys()),
        }
    return data, thumbnail


def _ffprobe(path: Path) -> dict[str, Any]:
    command = [
        "ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def analyze_video(path: Path, derivative_root: Path, asset_id: str, frame_count: int = 5) -> tuple[dict[str, Any], list[str]]:
    derivative_root.mkdir(parents=True, exist_ok=True)
    probe = _ffprobe(path)
    streams = probe.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    duration = _safe_float(probe.get("format", {}).get("duration")) or _safe_float(video.get("duration"))
    fps_value = video.get("r_frame_rate", "0/1")
    try:
        numerator, denominator = fps_value.split("/")
        fps = float(numerator) / float(denominator) if float(denominator) else None
    except (ValueError, AttributeError, ZeroDivisionError):
        fps = None
    frame_paths: list[str] = []
    count = max(3, min(frame_count, 12))
    for index in range(count):
        # Seeking exactly to the final timestamp often lands after the last decodable frame.
        timestamp = 0 if not duration else min(duration * index / max(count - 1, 1), max(duration - 0.05, 0))
        frame_path = derivative_root / f"{asset_id}-{index:02d}.jpg"
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(timestamp), "-i", str(path),
            "-frames:v", "1", "-vf", "scale='min(1600,iw)':-2", "-y", str(frame_path),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0 and frame_path.exists():
            frame_paths.append(str(frame_path))
    data = {
        "width": video.get("width"),
        "height": video.get("height"),
        "duration_seconds": duration,
        "fps": fps,
        "codec": video.get("codec_name"),
        "container": probe.get("format", {}).get("format_name"),
        "bit_rate": _safe_float(probe.get("format", {}).get("bit_rate")),
        "has_audio": audio is not None,
        "representative_frame_count": len(frame_paths),
    }
    return data, frame_paths


def analyze_media(path: Path, derivative_root: Path, asset_id: str) -> tuple[dict[str, Any], list[str]]:
    media_type = media_type_for(path)
    if media_type == "photo":
        data, thumbnail = analyze_image(path, derivative_root, asset_id)
        return data, [str(thumbnail)]
    if media_type == "video":
        frame_count = 3
        return analyze_video(path, derivative_root, asset_id, frame_count)
    raise ValueError(f"unsupported media type: {path.suffix}")


def mime_for(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"
