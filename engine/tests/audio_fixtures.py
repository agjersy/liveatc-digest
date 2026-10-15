"""Synthetic audio for tests: tone bursts separated by silence, written as 16-bit mono WAV."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path


def write_bursts(
    path: Path, bursts: list[tuple[float, float]], total_s: float, rate: int = 16_000
) -> Path:
    """*bursts* are (start_s, end_s) spans of a 440 Hz tone; the rest is digital silence."""
    frames = bytearray()
    n = int(total_s * rate)
    for i in range(n):
        t = i / rate
        on = any(a <= t < b for a, b in bursts)
        sample = int(12_000 * math.sin(2 * math.pi * 440 * t)) if on else 0
        frames += struct.pack("<h", sample)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(frames))
    return path
