"""Split a recording into speech segments with offsets relative to the block start."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydub import AudioSegment
from pydub.silence import detect_nonsilent


@dataclass(frozen=True)
class Segment:
    index: int
    start_ms: int
    end_ms: int
    path: Path  # 16 kHz mono WAV, ready for ASR

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


def detect_format(path: Path) -> str:
    with path.open("rb") as fh:
        head = fh.read(12)
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "wav"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xe3"):
        return "mp3"
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"OggS"):
        return "ogg"
    return "mp3"


def load_audio(path: Path) -> AudioSegment:
    return AudioSegment.from_file(path, format=detect_format(path))


def segment_audio(
    source: Path,
    out_dir: Path,
    min_silence_ms: int = 400,
    silence_thresh_dbfs: float = -40.0,
    pad_ms: int = 150,
    min_segment_ms: int = 700,
    max_segment_ms: int = 30_000,
    merge_gap_ms: int = 300,
    seek_step_ms: int = 10,
) -> list[Segment]:
    """Return speech segments exported as 16 kHz mono WAV files under *out_dir*.

    Thresholds are relative to the normalized recording so quiet scanner feeds behave like
    loud ones. Long stretches without a detectable gap are cut at *max_segment_ms*.
    """
    audio = load_audio(source)
    if len(audio) == 0:
        return []
    audio = audio.set_channels(1).set_frame_rate(16_000)
    gain = -20.0 - audio.dBFS if audio.dBFS != float("-inf") else 0.0
    audio = audio.apply_gain(gain)

    ranges = detect_nonsilent(
        audio,
        min_silence_len=min_silence_ms,
        silence_thresh=silence_thresh_dbfs,
        seek_step=seek_step_ms,
    )

    merged: list[list[int]] = []
    for start, end in ranges:
        if merged and start - merged[-1][1] <= merge_gap_ms:
            merged[-1][1] = end
        else:
            merged.append([start, end])

    out_dir.mkdir(parents=True, exist_ok=True)
    segments: list[Segment] = []
    total = len(audio)
    for start, end in merged:
        cursor = start
        while cursor < end:
            piece_end = min(end, cursor + max_segment_ms)
            if piece_end - cursor >= min_segment_ms:
                s = max(0, cursor - pad_ms)
                e = min(total, piece_end + pad_ms)
                idx = len(segments)
                path = out_dir / f"seg-{idx:04d}.wav"
                audio[s:e].export(path, format="wav")
                segments.append(Segment(idx, s, e, path))
            cursor = piece_end
    return segments
