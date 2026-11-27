"""Reliability fixes and selection controls added after real end-to-end runs."""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from clipping import selection

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def engine(monkeypatch):
    for name, symbol in (("yt_dlp", "YoutubeDL"), ("faster_whisper", "WhisperModel")):
        stub = ModuleType(name)
        setattr(stub, symbol, object)
        monkeypatch.setitem(sys.modules, name, stub)
    spec = importlib.util.spec_from_file_location("_reliability_engine", ROOT / "clipping" / "engine.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- honest render results -------------------------------------------------

def test_summarize_render_requires_success_and_file(tmp_path):
    from clipping.runner import summarize_render

    good = tmp_path / "a.mp4"
    good.write_bytes(b"x")
    ok, failed, lines = summarize_render([
        {"rank": 1, "status": "success", "video_path": str(good), "duration": 40},
        {"rank": 2, "status": "success", "video_path": str(tmp_path / "missing.mp4")},
        {"rank": 3, "status": "failed", "error": "ffmpeg died\nError opening output file h_2.ts."},
    ])
    assert (ok, failed) == (1, 2)
    assert lines[1].endswith("FAILED: video file missing")
    assert lines[2].endswith("FAILED: Error opening output file h_2.ts.")
    assert summarize_render(None) == (0, 0, [])


def test_exit_code_is_nonzero_unless_every_clip_rendered(tmp_path, capsys):
    import main

    good = tmp_path / "a.mp4"
    good.write_bytes(b"x")
    ok_row = {"rank": 1, "status": "success", "video_path": str(good)}
    bad_row = {"rank": 2, "status": "failed", "error": "boom"}
    assert main._print_render_summary([ok_row], str(tmp_path)) == 0
    assert main._print_render_summary([ok_row, bad_row], str(tmp_path)) == 1
    assert main._print_render_summary([bad_row], str(tmp_path)) == 1
    assert main._print_render_summary([], str(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "Selesai sebagian: 1 berhasil, 1 GAGAL" in out and "tidak ada klip yang berhasil" in out


def test_render_retry_then_loudnorm(monkeypatch, tmp_path):
    from clipping import postprocess, runner

    calls, norm = [], []
    video = tmp_path / "clip.mp4"

    def fake_render(rank, klip, *a, **k):
        calls.append(rank)
        if len(calls) == 1:
            return {"rank": rank, "status": "failed", "error": "transient"}
        video.write_bytes(b"x")
        return {"rank": rank, "status": "success", "video_path": str(video)}

    monkeypatch.setattr(runner.studio, "proses_klip", fake_render)
    monkeypatch.setattr(postprocess, "normalize_loudness", lambda p: norm.append(p) or True)
    cfg = SimpleNamespace(render_retries=1, loudnorm=True, pilihan_rasio="9:16")
    res = runner._render_with_retry({"rank": 1}, None, [], cfg, "libx264", None)
    assert res["status"] == "success" and res["render_attempts"] == 2 and res["loudness_normalized"]
    assert norm == [str(video)]

    calls.clear()
    monkeypatch.setattr(runner.studio, "proses_klip", lambda rank, *a, **k: calls.append(rank) or
                        {"rank": rank, "status": "failed", "error": "always"})
    res = runner._render_with_retry({"rank": 2}, None, [], SimpleNamespace(render_retries=0, loudnorm=True,
                                                                           pilihan_rasio="9:16"), "x", None)
    assert res["status"] == "failed" and calls == [2] and "loudness_normalized" not in res


# --- Gemini overload handling ----------------------------------------------

class _Overloaded(Exception):
    def __init__(self, code=503):
        super().__init__(f"{code} UNAVAILABLE. This model is currently experiencing high demand.")
        self.status_code = code


def _client(responses, models):
    def generate_content(model, contents, config):
        models.append(model)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(text=item)

    return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))


def test_overloaded_primary_switches_to_fallback_quickly(engine, monkeypatch):
    sleeps, models = [], []
    monkeypatch.setattr(engine.time, "sleep", sleeps.append)
    client = _client([_Overloaded(), _Overloaded(429), "[]"], models)
    assert engine._generate_json_with_retry(client, "primary", "fallback", "c", None) == []
    assert models == ["primary", "primary", "fallback"]
    assert sleeps == [engine.INITIAL_WAIT_SECONDS]


def test_fallback_gets_retries_then_reports_both_errors(engine, monkeypatch):
    sleeps, models = [], []
    monkeypatch.setattr(engine.time, "sleep", sleeps.append)
    client = _client([_Overloaded(), _Overloaded(), _Overloaded(), _Overloaded(), "[1]"], models)
    assert engine._generate_json_with_retry(client, "p", "f", "c", None) == [1]
    assert models == ["p", "p", "f", "f", "f"]
    client = _client([ValueError("bad key"), ValueError("still bad")], models := [])
    with pytest.raises(RuntimeError, match="utama & fallback"):
        engine._generate_json_with_retry(client, "p", "f", "c", None)
    assert models == ["p", "f"]


# --- keys and environment --------------------------------------------------

@pytest.mark.parametrize("value, expected", [
    ("your-pexels-api-key-here", ""), ("your-gemini-api-key-here", ""), ("  real-key-123 ", "real-key-123"),
    ("", ""), ("changeme", ""),
])
def test_placeholder_api_keys_count_as_missing(monkeypatch, value, expected):
    from clipping.config import _env_key

    monkeypatch.setenv("PEXELS_API_KEY", value)
    assert _env_key("PEXELS_API_KEY") == expected


def test_xet_downloads_disabled_by_default():
    import clipping  # noqa: F401

    assert os.environ.get("HF_HUB_DISABLE_XET") in ("1", "0")  # setdefault never overrides a user value


# --- selection controls ----------------------------------------------------

@pytest.mark.parametrize("text, seconds", [("90", 90), ("1:30", 90), ("01:02:03", 3723), ("2:05.5", 125.5)])
def test_parse_timestamp(text, seconds):
    assert selection.parse_timestamp(text) == seconds


@pytest.mark.parametrize("bad", ["2:00", "5:00-2:00", "a-b", "1:2:3:4-5"])
def test_invalid_time_ranges(bad):
    with pytest.raises(ValueError):
        selection.parse_time_range(bad)


def test_length_presets_and_overrides():
    assert selection.resolve_lengths("medium", None, None) == (30, 60)
    assert selection.resolve_lengths("short", None, 45) == (15, 45)
    with pytest.raises(ValueError):
        selection.resolve_lengths("short", 40, 20)


def test_filter_transcript_keeps_overlapping_lines_and_their_markers():
    text = ("[0.0 - 50.0] intro\n[CHAPTER at 60.0s] Big\n[55.0 - 70.0] middle [EVENT: 3 rapid scene cuts]\n"
            "[200.0 - 210.0] outro\n")
    assert selection.filter_transcript(text, (60, 120)) == "[55.0 - 70.0] middle [EVENT: 3 rapid scene cuts]\n"
    assert selection.filter_transcript(text, None) == text


def test_enforce_duration_prefers_in_range_but_never_leaves_user_empty():
    mk = lambda s, e: {"start_time": s, "end_time": e}
    clips = [mk(0, 45), mk(100, 200), mk(300, 312), mk(400, 452)]   # 45s, 100s, 12s, 52s
    ok, rejected = selection.enforce_duration(clips, 30, 60, keep=2)
    assert ok == [clips[0], clips[3]] and rejected == [clips[2], clips[1]]
    ok, _ = selection.enforce_duration(clips, 30, 60, keep=3)       # needs one out-of-range: closest (12s)
    assert ok == [clips[0], clips[2], clips[3]]
    ok, _ = selection.enforce_duration([mk(0, 64)], 30, 60, keep=1)  # within the 5 s tolerance
    assert ok == [mk(0, 64)]


def test_prompt_uses_length_and_topic(engine):
    cfg = SimpleNamespace(min_clip_duration=15, max_clip_duration=30, topic='sleep "and" dopamine')
    en = engine.get_analysis_prompt("[0.0 - 5.0] hi", 2, 3, cfg=cfg)
    assert "Clip duration must be 15-30 seconds." in en
    assert "USER FOCUS (REQUIRED)" in en and "clips about: \"sleep 'and' dopamine\"" in en
    idn = engine.get_analysis_prompt("[0.0 - 5.0] hi", 2, 3, cfg=cfg, prompt_language="id")
    assert "FOKUS DARI USER (WAJIB)" in idn and "15-30 detik" in idn
    assert "USER FOCUS" not in engine.get_analysis_prompt("[0.0 - 5.0] hi", 2, 3)


def test_cli_selection_flags(tmp_path, monkeypatch):
    from clipping.config import build_config

    monkeypatch.chdir(tmp_path)
    cfg = build_config(["--url", "https://youtube.com/watch?v=x"])
    assert (cfg.min_clip_duration, cfg.max_clip_duration, cfg.loudnorm, cfg.render_retries) == (30, 60, True, 1)
    assert cfg.topic is None and cfg.time_range is None and not cfg.flat_output
    cfg = build_config(["--url", "https://youtube.com/watch?v=x", "--clip-length", "short", "--topic", " money ",
                        "--time-range", "1:00-5:00", "--no-loudnorm", "--render-retries", "0", "--flat-output"])
    assert (cfg.min_clip_duration, cfg.max_clip_duration, cfg.topic, cfg.time_range) == (15, 30, "money", (60.0, 300.0))
    assert not cfg.loudnorm and cfg.render_retries == 0 and cfg.flat_output
    for bad in (["--time-range", "5:00-1:00"], ["--min-duration", "50", "--max-duration", "40"],
                ["--render-retries", "9"]):
        with pytest.raises(SystemExit):
            build_config(["--url", "https://youtube.com/watch?v=x", *bad])


# --- loudness normalization ------------------------------------------------

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_loudnorm_raises_quiet_audio_and_copies_video(tmp_path):
    from clipping import events, postprocess

    clip = tmp_path / "quiet.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=12",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=12,volume=0.02",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(clip)], check=True)
    before = sorted(db for _, db in events.measure_loudness(str(clip)))[6]
    probe = lambda: subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                    "stream=codec_name,nb_frames", "-of", "csv=p=0", str(clip)],
                                   capture_output=True, text=True).stdout.strip()
    video_before = probe()
    assert postprocess.normalize_loudness(str(clip)) is True
    after = sorted(db for _, db in events.measure_loudness(str(clip)))[6]
    assert after - before > 15          # roughly -46 dB -> about -17 dB RMS
    assert probe() == video_before      # picture stream copied, not re-encoded
    assert not list(tmp_path.glob("*.loudnorm.mp4"))
    assert postprocess.normalize_loudness(str(tmp_path / "missing.mp4")) is False
