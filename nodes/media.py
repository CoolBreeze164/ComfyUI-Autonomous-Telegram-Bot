"""Portable audio conversion and decoding using PyAV's bundled media libraries."""

import io
from fractions import Fraction

import numpy as np
from comfy.model_management import throw_exception_if_processing_interrupted


def _av():
    try:
        import av
    except ImportError:
        raise RuntimeError(
            "Audio processing needs PyAV (av). Install this pack's "
            "requirements.txt with ComfyUI's Python, then restart ComfyUI. "
            "No ffmpeg executable or PATH changes are needed."
        ) from None
    return av


class _AudioEncoder:
    def __init__(self, av, output, codec, rate, layout, bit_rate):
        self.output = output
        self.stream = output.add_stream(codec, rate=rate)
        self.stream.layout = layout
        self.stream.bit_rate = bit_rate
        self.stream.time_base = Fraction(1, rate)
        sample_format = self.stream.codec_context.codec.audio_formats[0].name
        self.stream.codec_context.format = sample_format
        self.resampler = av.AudioResampler(format=sample_format, layout=layout, rate=rate)
        self.rate = rate
        self.next_pts = 0

    def _encode(self, frames):
        for frame in frames:
            throw_exception_if_processing_interrupted()
            pts = self.next_pts if frame.pts is None else round(frame.pts * frame.time_base * self.rate)
            frame.pts = pts
            frame.time_base = Fraction(1, self.rate)
            self.next_pts = pts + frame.samples
            self.output.mux(self.stream.encode(frame))

    def write(self, frame):
        # WAV input is a continuous sample sequence. Let the output sample clock
        # determine timestamps after resampling.
        frame.pts = None
        self._encode(self.resampler.resample(frame))

    def finish(self):
        self._encode(self.resampler.resample(None))
        self.output.mux(self.stream.encode(None))


def convert_wav_bytes(input_bytes, output_format="mp3"):
    if output_format not in {"mp3", "ogg"}:
        raise ValueError("Audio output format must be mp3 or ogg (Opus).")
    av = _av()
    with av.open(io.BytesIO(input_bytes), format="wav") as source:
        if not source.streams.audio:
            raise ValueError("The input contains no audio stream.")
        incoming = source.streams.audio[0]
        layout = "mono" if incoming.codec_context.channels == 1 else "stereo"
        codec, rate, bit_rate = ("libopus", 48000, 96000) if output_format == "ogg" else ("libmp3lame", 44100, 192000)
        with io.BytesIO() as buffer:
            with av.open(buffer, mode="w", format=output_format) as output:
                encoder = _AudioEncoder(av, output, codec, rate, layout, bit_rate)
                samples = 0
                for frame in source.decode(incoming):
                    throw_exception_if_processing_interrupted()
                    samples += frame.samples
                    encoder.write(frame)
                if not samples:
                    raise ValueError("The input contains no audio samples.")
                encoder.finish()
            return buffer.getvalue()


def audio_from_bytes(data):
    import torch
    av = _av()
    chunks = []
    resampler = av.AudioResampler(format="fltp", layout="stereo", rate=48000)
    with av.open(io.BytesIO(data)) as source:
        if not source.streams.audio:
            raise ValueError("Telegram file contained no audio stream.")
        for frame in source.decode(source.streams.audio[0]):
            throw_exception_if_processing_interrupted()
            frame.pts = None
            chunks.extend(item.to_ndarray().copy() for item in resampler.resample(frame))
        chunks.extend(item.to_ndarray().copy() for item in resampler.resample(None))
    if not chunks or not any(item.shape[1] for item in chunks):
        raise ValueError("Telegram audio file contained no samples.")
    array = np.concatenate(chunks, axis=1).astype(np.float32, copy=False)
    return {"waveform": torch.from_numpy(array).unsqueeze(0), "sample_rate": 48000}

