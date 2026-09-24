"""Media helpers and the original suite's Parse JSON node (MIT; see licenses/)."""

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
    paths = [str(path) for path in video[1] if Path(path).suffix.lower() not in {".png", ".json"}]
    if not paths:
        raise ValueError("The VHS_FILENAMES input contains no video file.")
    return paths[-1]


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
