"""WAV checks and splitting.

Chirp 3 BatchRecognize handles ~1 h of audio, but only ~20 min per file when
word-level timestamps are on. Sessions longer than CHUNK_SECONDS are split,
preferring a quiet spot near each boundary so words aren't cut in half.
Pure stdlib (wave/array) - no ffmpeg needed in the container.
"""
from __future__ import annotations

import array
import os
import wave
from dataclasses import dataclass

DEFAULT_CHUNK_SECONDS = int(os.environ.get("CHUNK_SECONDS", "1140"))  # 19 min, under the 20-min limit
SEARCH_WINDOW_SECONDS = 15   # look this far back from a boundary for a quiet spot
FRAME_MS = 100


@dataclass
class WavInfo:
    sample_rate: int
    channels: int
    sample_width: int
    frames: int

    @property
    def duration_s(self) -> float:
        return self.frames / float(self.sample_rate or 1)

    @property
    def is_recorder_format(self) -> bool:
        return self.sample_rate == 16000 and self.channels == 1 and self.sample_width == 2


def read_wav_info(path: str) -> WavInfo:
    with wave.open(path, "rb") as w:
        return WavInfo(w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes())


def _quietest_frame(samples: array.array, start: int, end: int, step: int) -> int:
    best, best_energy = end, None
    for pos in range(max(start, 0), max(end - step, 0) + 1, step):
        seg = samples[pos:pos + step]
        if not seg:
            break
        energy = sum(abs(x) for x in seg[::8])
        if best_energy is None or energy < best_energy:
            best, best_energy = pos, energy
    return best


def plan_chunks(path: str, chunk_seconds: int = DEFAULT_CHUNK_SECONDS) -> list[tuple[int, int]]:
    """Return [(start_frame, end_frame), ...] covering the whole file."""
    info = read_wav_info(path)
    total = info.frames
    max_len = int(chunk_seconds * info.sample_rate)
    if total <= max_len:
        return [(0, total)]
    with wave.open(path, "rb") as w:
        samples = array.array("h", w.readframes(total))
    step = int(info.sample_rate * FRAME_MS / 1000)
    search = int(SEARCH_WINDOW_SECONDS * info.sample_rate)
    bounds, start = [], 0
    while total - start > max_len:
        target = start + max_len
        cut = _quietest_frame(samples, target - search, target, step)
        if cut <= start:
            cut = target
        bounds.append((start, cut))
        start = cut
    bounds.append((start, total))
    return bounds


def write_chunks(path: str, bounds: list[tuple[int, int]], out_dir: str) -> list[tuple[str, float]]:
    """Write chunk WAVs; return [(chunk_path, offset_seconds), ...]."""
    os.makedirs(out_dir, exist_ok=True)
    out = []
    with wave.open(path, "rb") as src:
        rate = src.getframerate()
        for i, (a, b) in enumerate(bounds):
            src.setpos(a)
            data = src.readframes(b - a)
            p = os.path.join(out_dir, f"part_{i:02d}.wav")
            with wave.open(p, "wb") as dst:
                dst.setnchannels(src.getnchannels())
                dst.setsampwidth(src.getsampwidth())
                dst.setframerate(rate)
                dst.writeframes(data)
            out.append((p, a / float(rate)))
    return out
