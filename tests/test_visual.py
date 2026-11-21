"""Visual review of candidate clips (frames -> Gemini -> visual component)."""

import shutil
import subprocess
from types import SimpleNamespace

import pytest

from clipping import scoring, visual

TRANSCRIPT = "[0.0 - 5.0] first words\n[5.0 - 10.0] second part\n[10.0 - 15.0] third bit\n"


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    path = tmp_path_factory.mktemp("vis") / "v.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc=size=640x360:rate=10:duration=15", "-pix_fmt", "yuv420p", str(path)], check=True)
    return str(path)


def test_frame_times_spread_inside_the_clip():
    assert visual.frame_times(10, 22, 6) == [11.0, 13.0, 15.0, 17.0, 19.0, 21.0]
    assert visual.frame_times(5, 5) == [] and visual.frame_times(0, 10, 0) == []


def test_transcript_excerpt_selects_overlapping_lines():
    assert visual.transcript_excerpt(TRANSCRIPT, 4, 9) == "first words second part"
    assert visual.transcript_excerpt(TRANSCRIPT, 0, 15, max_chars=15) == "first words ..."


def test_extract_frame_returns_small_jpeg(video):
    data = visual.extract_frame(video, 3.0, width=256)
    assert data[:2] == b"\xff\xd8" and len(data) < 60_000
    with pytest.raises(RuntimeError):
        visual.extract_frame(video + ".missing", 1.0)


def test_build_parts_layout(video):
    from google.genai import types

    clips = [{"start_time": 0, "end_time": 6, "title_inggris": "A"},
             {"start_time": 6, "end_time": 14, "title_inggris": "B"}]
    parts = visual.build_parts(clips, video, TRANSCRIPT, types, frames=3)
    assert len(parts) == 1 + 2 * (1 + 3)
    assert "3 frames sampled" in parts[0].text
    assert parts[1].text.startswith("\nCANDIDATE 1 | 0.0s - 6.0s | title: A")
    assert "transcript: first words second part" in parts[1].text
    assert all(p.inline_data.mime_type == "image/jpeg" for p in parts[2:5])
    assert parts[5].text.startswith("\nCANDIDATE 2")


def test_apply_scores_maps_clamps_and_skips_bad_items():
    clips = [{}, {}, {}]
    n = visual.apply_scores(clips, [
        {"candidate": 1, "visual_score": 140, "visual_notes": "big celebration"},
        {"candidate": 3, "visual_score": "35", "visual_notes": "static slide"},
        {"candidate": 9, "visual_score": 50, "visual_notes": "out of range"},
        {"candidate": "x", "visual_score": 50},
    ])
    assert n == 2
    assert clips[0] == {"visual_score": 100, "visual_notes": "big celebration"}
    assert clips[1] == {} and clips[2]["visual_score"] == 35
    assert visual.apply_scores(clips, {"not": "a list"}) == 0


def test_score_candidates_sends_one_request_with_images(video):
    seen = {}

    def fake_generate_json(**kwargs):
        seen.update(kwargs)
        return [{"candidate": 1, "visual_score": 80, "visual_notes": "action"},
                {"candidate": 2, "visual_score": 20, "visual_notes": "dull"}]

    clips = [{"start_time": 0, "end_time": 6}, {"start_time": 6, "end_time": 14}]
    cfg = SimpleNamespace(api_key_gemini="synthetic", gemini_model="m", gemini_fallback_model="f",
                          ai_temperature=0.3, visual_frames=2)
    assert visual.score_candidates(clips, video, TRANSCRIPT, cfg, fake_generate_json) == 2
    assert (seen["model"], seen["fallback_model"]) == ("m", "f")
    content = seen["contents"][0]
    assert len(content.parts) == 1 + 2 * (1 + 2)
    assert seen["config"].temperature == 0.3 and seen["config"].response_mime_type == "application/json"
    assert [c["visual_score"] for c in clips] == [80, 20]


def test_visual_component_reranks_candidates():
    clips = [
        {"viral_score": 90, "start_time": 0, "end_time": 30, "visual_score": 20},   # great words, dull picture
        {"viral_score": 84, "start_time": 40, "end_time": 70, "visual_score": 95},  # visible celebration
    ]
    top = scoring.rerank(clips, None, keep=1)
    assert top[0]["start_time"] == 40
    assert top[0]["score_components"] == {"ai": 0.84, "visual": 0.95}
    # (0.5*0.84 + 0.3*0.95) / 0.8 = 0.88125
    assert top[0]["viral_score"] == 88
    # all-zero visual scores don't count (uninformative)
    flat = scoring.rerank([{"viral_score": 90, "start_time": 0, "end_time": 30, "visual_score": 0}], None, keep=1)
    assert flat[0]["score_components"] == {"ai": 0.9} and flat[0]["viral_score"] == 90


def test_visual_review_enabled_rules():
    from clipping import runner

    on = SimpleNamespace(visual_rerank=True, load_gemini_json=False, ai_provider="gemini")
    assert runner._visual_review_enabled(on)
    assert not runner._visual_review_enabled(SimpleNamespace(visual_rerank=False))
    assert not runner._visual_review_enabled(SimpleNamespace(visual_rerank=True, load_gemini_json=True))
    assert not runner._visual_review_enabled(
        SimpleNamespace(visual_rerank=True, load_gemini_json=False, ai_provider="nvidia"))
    assert scoring.candidate_pool(3, None, force=True) == 6 and scoring.candidate_pool(3, None) == 3


def test_cli_visual_flags(tmp_path, monkeypatch):
    from clipping.config import build_config

    monkeypatch.chdir(tmp_path)
    cfg = build_config(["--url", "https://youtube.com/watch?v=x"])
    assert not cfg.visual_rerank and cfg.visual_frames == 6
    cfg = build_config(["--url", "https://youtube.com/watch?v=x", "--visual-rerank", "--visual-frames", "4"])
    assert cfg.visual_rerank and cfg.visual_frames == 4
    with pytest.raises(SystemExit):
        build_config(["--url", "https://youtube.com/watch?v=x", "--visual-frames", "20"])
