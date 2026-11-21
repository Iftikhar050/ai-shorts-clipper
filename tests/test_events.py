"""FFmpeg audio/visual event detection."""

import shutil
import subprocess

import pytest

from clipping import events

LOUDNESS_OUT = """frame:0    pts:0       pts_time:0
lavfi.astats.Overall.RMS_level=-30.5
frame:1    pts:8000    pts_time:1
lavfi.astats.Overall.RMS_level=-inf
frame:2    pts:16000   pts_time:2
lavfi.astats.Overall.RMS_level=-12.0
"""
SCENE_OUT = """frame:0 pts:0 pts_time:0
lavfi.scene_score=0.000000
frame:1 pts:1 pts_time:0.25
lavfi.scene_score=0.912
frame:2 pts:2 pts_time:0.5
lavfi.scene_score=0.20
"""


def test_parsers():
    assert events.parse_loudness(LOUDNESS_OUT) == [(0.0, -30.5), (1.0, -90.0), (2.0, -12.0)]
    assert events.parse_scene_cuts(SCENE_OUT) == [0.25]
    assert events.parse_scene_cuts(SCENE_OUT, threshold=0.1) == [0.25, 0.5]


def test_find_spikes_groups_loud_seconds_against_the_median():
    loud = [(float(t), -30.0) for t in range(30)]
    loud[10] = (10.0, -20.0)          # +10 dB
    loud[11] = (11.0, -15.0)          # +15 dB, merges with 10
    loud[20] = (20.0, -21.0)          # +9 dB, separate spike
    loud[25] = (25.0, -90.0)          # silence ignored in the baseline
    baseline, spikes = events.find_spikes(loud)
    assert baseline == -30.0
    assert spikes == [{"start": 10.0, "end": 12.0, "delta_db": 15.0},
                      {"start": 20.0, "end": 21.0, "delta_db": 9.0}]
    assert events.find_spikes([(0.0, -20.0)] * 5) == (None, [])  # too little audio


def test_spikes_use_local_context_not_the_whole_video():
    # quiet first half, loud second half: the step itself is not a spike,
    # but a burst inside the loud half is
    loud = [(float(t), -40.0) for t in range(60)] + [(float(t), -20.0) for t in range(60, 120)]
    loud[90] = (90.0, -12.0)                     # +8 dB above its loud neighbours
    _, spikes = events.find_spikes(loud)
    assert [s["start"] for s in spikes] == [90.0]
    # compressed broadcast audio (~2 dB range) produces no spikes
    flat = [(float(t), -22.0 + (t % 5) * 0.5) for t in range(300)]
    assert events.find_spikes(flat)[1] == []


def test_find_cut_bursts_merges_overlapping_windows():
    assert events.find_cut_bursts([1, 2, 3, 4, 30, 31, 50]) == [{"start": 1, "end": 4, "cuts": 4}]
    assert events.find_cut_bursts([1, 20, 40]) == []


def test_tags_and_audio_score():
    ev = {"spikes": [{"start": 10, "end": 12, "delta_db": 12.4}],
          "cut_bursts": [{"start": 30, "end": 34, "cuts": 5}]}
    assert events.tags_for_window(ev, 9, 11) == ["volume spike +12 dB"]
    assert events.tags_for_window(ev, 33, 40) == ["5 rapid scene cuts"]
    assert events.tags_for_window(ev, 13, 29) == [] and events.tags_for_window(None, 0, 1) == []
    assert events.audio_score(ev, 0, 60) == 0.62 and events.audio_score(ev, 40, 60) == 0.0
    assert events.has_events(ev) and not events.has_events({"spikes": [], "cut_bursts": []})


def test_collect_events_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ffmpeg missing")

    monkeypatch.setattr(events, "_run_ffmpeg", boom)
    ev = events.collect_events("nope.mp4")
    assert ev["spikes"] == [] and ev["cut_bursts"] == [] and len(ev["errors"]) == 2
    assert events.collect_events("nope.mp4", scenes=False)["errors"] == ["loudness: ffmpeg missing"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_real_ffmpeg_finds_planted_spike_and_cut_burst(tmp_path):
    video = tmp_path / "events.mp4"
    graph = (
        "color=red:s=160x120:r=10:d=8[a];color=blue:s=160x120:r=10:d=1[b];"
        "color=green:s=160x120:r=10:d=1[c];color=black:s=160x120:r=10:d=1[d];"
        "color=white:s=160x120:r=10:d=1[e];color=gray:s=160x120:r=10:d=8[f];"
        "[a][b][c][d][e][f]concat=n=6:v=1:a=0[v];"
        "sine=frequency=440:duration=20,volume='if(between(t,14,16),1.0,0.02)':eval=frame[aout]"
    )
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-filter_complex", graph,
                    "-map", "[v]", "-map", "[aout]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(video)], check=True)
    ev = events.collect_events(str(video))
    assert ev["errors"] == []
    assert len(ev["spikes"]) == 1 and 13 <= ev["spikes"][0]["start"] <= 15 and ev["spikes"][0]["delta_db"] > 20
    assert len(ev["cut_bursts"]) == 1 and 7 <= ev["cut_bursts"][0]["start"] <= 9
