"""Media helpers and the original suite's Parse JSON node (MIT; see licenses/)."""

import os
import io
import json
import mimetypes
import ntpath
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from .media import audio_from_bytes, convert_wav_bytes

UINT64_MIN = -9223372036854775808
UINT64_MAX = 9223372036854775807

def _allowed_media_roots():
    """
    Return the resolved ComfyUI output/temp directories.

    This fails closed if ComfyUI's folder_paths helper is unavailable or no
    usable directories are configured.
    """
    try:
        import folder_paths
    except Exception as exc:
        raise ValueError(
            "ComfyUI folder_paths is unavailable; refusing to open media paths."
        ) from exc

    roots = []

    for attr in ("get_output_directory", "get_temp_directory"):
        getter = getattr(folder_paths, attr, None)
        if not callable(getter):
            continue

        try:
            root = getter()
        except Exception:
            continue

        if root:
            roots.append(Path(root))

    resolved_roots = []

    for root in roots:
        try:
            root = Path(root).expanduser()
            root = Path(os.path.realpath(root))
        except OSError:
            continue

        if root.is_dir():
            resolved_roots.append(root)

    # De-duplicate while preserving order.
    unique_roots = []
    for root in resolved_roots:
        if root not in unique_roots:
            unique_roots.append(root)

    if not unique_roots:
        raise ValueError(
            "No usable ComfyUI output/temp directories are configured."
        )

    return unique_roots


def validate_media_path(path):
    """
    Resolve the supplied path and require it to be inside one of the allowed
    ComfyUI media roots.

    This deliberately resolves symlinks with realpath before checking the path.
    """
    raw = str(path)

    if not raw:
        raise ValueError("The media path is empty.")

    if "\x00" in raw:
        raise ValueError("The media path contains an invalid NUL byte.")

    try:
        real = Path(os.path.realpath(Path(raw).expanduser()))
    except OSError as exc:
        raise ValueError("Could not resolve the media path.") from exc

    real_norm = os.path.normcase(str(real))

    for root in _allowed_media_roots():
        root_norm = os.path.normcase(str(root))

        try:
            common = os.path.commonpath([real_norm, root_norm])
        except ValueError:
            # Different drives / incompatible paths.
            continue

        if common == root_norm:
            if not real.is_file():
                raise ValueError(
                    "The media path must point to an existing regular file."
                )

            return str(real)

    raise ValueError(
        "Media paths must be inside the ComfyUI output or temp directories."
    )


def open_media_file(path):
    """
    Validate immediately before opening.
    """
    return open(validate_media_path(path), "rb")

def cleanup_params(params):
    params = dict(params)
    if params.get("parse_mode") == "None":
        params.pop("parse_mode")
    if params.get("message_thread_id", 0) <= 0:
        params.pop("message_thread_id", None)
    return params


def guess_mimetype(file_name):
    return mimetypes.guess_type(file_name)[0] or "application/octet-stream"


def media_basename(path):
    return ntpath.basename(str(path))


def video_file_path(video):
    paths = [
        str(path)
        for path in video[1]
        if Path(str(path)).suffix.lower() not in {".png", ".json"}
    ]

    if not paths:
        raise ValueError("The VHS_FILENAMES input contains no video file.")
    return validate_media_path(paths[-1])

def images_to_bytes(images, format="PNG"):
    encoded = []
    for tensor in images:
        array = np.clip(255.0 * tensor.detach().cpu().numpy(), 0, 255).astype(np.uint8)
        image = Image.fromarray(array)
        with io.BytesIO() as buffer:
            image.save(buffer, format="JPEG" if format.upper() == "JPG" else format)
            encoded.append(buffer.getvalue())
    if not encoded:
        raise ValueError("The IMAGE batch is empty.")
    return encoded


def image_from_bytes(data):
    import torch
    with Image.open(io.BytesIO(data)) as source:
        rgb = ImageOps.exif_transpose(source).convert("RGB")
        array = np.asarray(rgb, dtype=np.float32).copy() / 255.0
    return torch.from_numpy(array).unsqueeze(0)


def audio_to_wav_bytes(audio, format="WAV"):
    # A small PCM writer avoids torchaudio/torchcodec ABI issues on recent Torch.
    # It uses only the same numpy/torch dependencies already present in ComfyUI.
    waveform = audio["waveform"].detach().cpu().numpy()
    if waveform.ndim == 3:
        if waveform.shape[0] != 1:
            raise ValueError("Send Audio accepts one AUDIO item (batch size 1).")
        waveform = waveform[0]
    if waveform.ndim == 1:
        waveform = waveform[np.newaxis, :]
    if waveform.ndim != 2 or not waveform.size:
        raise ValueError("Expected a nonempty AUDIO waveform [1, channels, samples].")
    pcm = (np.clip(waveform, -1, 1).T * 32767).astype("<i2").tobytes()
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(waveform.shape[0])
            handle.setsampwidth(2)
            handle.setframerate(int(audio.get("sample_rate", 44100)))
            handle.writeframes(pcm)
        return buffer.getvalue()


class ParseJSON:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"json_string": ("STRING", {"multiline": True})}}

    RETURN_TYPES = ("DICT",)
    RETURN_NAMES = ("DICT",)
    FUNCTION = "parse_json"
    CATEGORY = "Autonomous Telegram Bot ◀️/experimental"

    def parse_json(self, json_string):
        return (json.loads(json_string),)
