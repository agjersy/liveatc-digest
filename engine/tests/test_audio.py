from liveatc_digest.audio import detect_format, segment_audio
from tests.audio_fixtures import write_bursts


def test_segment_audio_finds_bursts_with_offsets(tmp_path):
    wav = write_bursts(tmp_path / "in.wav", [(1.0, 2.5), (4.0, 5.0)], total_s=6.0)
    assert detect_format(wav) == "wav"
    segs = segment_audio(wav, tmp_path / "segs")
    assert len(segs) == 2
    first, second = segs
    assert abs(first.start_ms - 1000) <= 200 and abs(first.end_ms - 2500) <= 200
    assert abs(second.start_ms - 4000) <= 200 and abs(second.end_ms - 5000) <= 200
    assert first.path.exists() and first.path.suffix == ".wav"


def test_segment_audio_merges_small_gaps_and_drops_blips(tmp_path):
    wav = write_bursts(tmp_path / "in.wav", [(1.0, 1.5), (1.6, 2.2), (3.0, 3.2)], total_s=4.0)
    segs = segment_audio(wav, tmp_path / "segs", merge_gap_ms=300, min_segment_ms=700)
    assert len(segs) == 1
    assert abs(segs[0].start_ms - 1000) <= 200 and abs(segs[0].end_ms - 2200) <= 200


def test_detect_format_mp3(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"ID3\x04" + b"\x00" * 20)
    assert detect_format(p) == "mp3"
