"""Score breakdown, audience signals, transcript markers and combined re-ranking."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from clipping import scoring
from clipping.providers.schema import build_clips_schema, build_gemini_schema

ROOT = Path(__file__).resolve().parents[1]

INFO = {
    "duration": 120,
    "heatmap": [
        {"start_time": 0, "end_time": 30, "value": 0.2},
        {"start_time": 30, "end_time": 60, "value": 0.4},
        {"start_time": 60, "end_time": 90, "value": 0.8},  # most replayed -> 1.0 after normalizing
        {"start_time": 90, "end_time": 120, "value": 0.1},
    ],
    "chapters": [{"start_time": 0, "end_time": 60, "title": "Intro"},
                 {"start_time": 60, "end_time": 120, "title": "The big moment"}],
}
COMMENTS = [{"text": "1:05 😂😂", "like_count": 99}, {"text": "wait 1:10 and 0:05!", "like_count": 0},
            {"text": "no timestamp here", "like_count": 500}, {"text": "9:59 out of range", "like_count": 3}]


@pytest.fixture
def engine(monkeypatch):
    for name, symbol in (("yt_dlp", "YoutubeDL"), ("faster_whisper", "WhisperModel")):
        stub = ModuleType(name)
        setattr(stub, symbol, object)
        monkeypatch.setitem(sys.modules, name, stub)
    spec = importlib.util.spec_from_file_location("_scoring_engine", ROOT / "clipping" / "engine.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_score_breakdown_is_clamped_and_summed():
    clips = [{"viral_score": 99, "score_breakdown": {"hook": 25, "emotion": 0, "shareability": "15",
                                                     "clarity": 18.6, "payoff": None}},
             {"viral_score": 80}]
    scoring.apply_score_breakdown(clips)
    assert clips[0]["score_breakdown"] == {"hook": 20, "emotion": 1, "shareability": 15, "clarity": 19, "payoff": 1}
    assert clips[0]["viral_score"] == 56
    assert clips[1] == {"viral_score": 80}  # untouched without a breakdown


@pytest.mark.parametrize("text, expected", [
    ("3:15 lol", [195.0]), ("at 1:02:03!", [3723.0]), ("0:05 and 12:30", [5.0, 750.0]),
    ("score 12:345", []), ("ratio 3:1", []), ("10:61", []),
])
def test_comment_timestamps(text, expected):
    assert scoring.parse_comment_timestamps(text) == expected


def test_collect_signals_normalizes_heatmap_and_weights_comments():
    s = scoring.collect_signals(INFO, COMMENTS)
    assert [b["value"] for b in s["heatmap"]] == [0.25, 0.5, 1.0, 0.125]
    assert [c["title"] for c in s["chapters"]] == ["Intro", "The big moment"]
    assert [(m["time"], m["weight"]) for m in s["comment_marks"]] == [(65.0, 3.0), (70.0, 1.0), (5.0, 1.0)]
    assert s["comments_fetched"] == 4 and scoring.has_signals(s)
    empty = scoring.collect_signals({"heatmap": None}, None)
    assert not scoring.has_signals(empty) and empty["heatmap"] == []


def test_annotate_transcript_adds_markers_and_chapters():
    s = scoring.collect_signals(INFO, COMMENTS)
    text = "[2.0 - 8.0] hello there\n[62.0 - 68.0] the big moment\n[95.0 - 99.0] quiet bit\n"
    out = scoring.annotate_transcript(text, s)
    assert out.splitlines() == [
        "[CHAPTER at 0.0s] Intro",
        "[2.0 - 8.0] hello there [AUDIENCE: 1 viewer comment point here]",
        "[CHAPTER at 60.0s] The big moment",
        "[62.0 - 68.0] the big moment [AUDIENCE: replayed 100%, 2 viewer comments point here]",
        "[95.0 - 99.0] quiet bit",
    ]
    assert out.endswith("\n")
    assert scoring.annotate_transcript(text, None) == text
    assert scoring.annotate_transcript(text, scoring.collect_signals({}, None)) == text


def test_rerank_lets_audience_data_overtake_the_ai_and_explains_why():
    s = scoring.collect_signals(INFO, COMMENTS)
    clips = [
        {"rank": 1, "viral_score": 90, "start_time": 0, "end_time": 25},    # AI favourite, cold spot
        {"rank": 2, "viral_score": 75, "start_time": 60, "end_time": 85},   # replayed + commented
        {"rank": 3, "viral_score": 70, "start_time": 95, "end_time": 118},
    ]
    top = scoring.rerank(clips, s, keep=2)
    assert [c["start_time"] for c in top] == [60, 0]
    assert [c["rank"] for c in top] == [1, 2]
    best = top[0]
    assert best["ai_viral_score"] == 75
    assert set(best["score_components"]) == {"ai", "heatmap", "comments"}
    assert best["score_components"]["heatmap"] == 1.0
    assert best["audience_signals"] == {"heatmap_peak": 1.0, "comment_mentions": 2}
    assert best["viral_score"] > top[1]["viral_score"]


def test_rerank_skips_near_duplicate_candidates():
    s = scoring.collect_signals(INFO, None)
    clips = [
        {"viral_score": 92, "start_time": 45.5, "end_time": 90.8},
        {"viral_score": 87, "start_time": 52.0, "end_time": 90.8},   # 100% inside the first one
        {"viral_score": 70, "start_time": 0.0, "end_time": 40.0},
    ]
    top = scoring.rerank(clips, s, keep=2)
    assert [(c["start_time"], c["rank"]) for c in top] == [(45.5, 1), (0.0, 2)]
    # if nothing else qualifies, fewer clips are returned rather than a duplicate
    assert len(scoring.rerank([dict(clips[0]), dict(clips[1])], s, keep=2)) == 1


def test_rerank_uses_only_available_signals():
    s = scoring.collect_signals(INFO, None)  # heatmap only
    clip = scoring.rerank([{"viral_score": 90, "start_time": 60, "end_time": 80}], s, keep=1)[0]
    assert set(clip["score_components"]) == {"ai", "heatmap"}
    # (0.5*0.9 + 0.3*1.0) / 0.8 = 0.9375 -> 94 (comments weight dropped and renormalized)
    assert clip["viral_score"] == 94
    assert clip["audience_signals"]["comment_mentions"] is None


def test_weights_and_candidate_pool():
    assert scoring.parse_weights("") == scoring.DEFAULT_WEIGHTS
    assert scoring.parse_weights("heatmap=0.6, comments=0") == {"ai": 0.5, "heatmap": 0.6, "comments": 0.0}
    for bad in ("audio=1", "ai=x", "heatmap=-1", "ai=0"):
        with pytest.raises(ValueError):
            scoring.parse_weights(bad)
    s = scoring.collect_signals(INFO, None)
    assert scoring.candidate_pool(3, s) == 6 and scoring.candidate_pool(8, s) == 13
    assert scoring.candidate_pool(3, None) == 3


def test_schema_breakdown_is_optional_and_ordered():
    plain = build_clips_schema({"General": {}})
    assert "score_breakdown" not in plain["items"]["properties"]
    with_sb = build_clips_schema({"General": {}}, score_breakdown=True)
    props = list(with_sb["items"]["properties"])
    assert props[props.index("viral_score") + 1] == "score_breakdown"
    req = with_sb["items"]["required"]
    assert req[req.index("viral_score") + 1] == "score_breakdown"
    assert with_sb["items"]["properties"]["score_breakdown"]["required"] == list(scoring.BREAKDOWN_KEYS)
    gem = build_gemini_schema({"General": {}}, score_breakdown=True)["items"]
    assert list(gem["properties"])[2] == "score_breakdown" and gem["required"][2] == "score_breakdown"
    assert gem["properties"]["score_breakdown"]["type"] == "OBJECT"


@pytest.mark.parametrize("lang, header", [("en", "AUDIENCE SIGNALS (REAL VIEWER DATA)"),
                                          ("id", "SINYAL AUDIENS (DATA PENONTON ASLI)")])
def test_signals_prompt_section_only_with_markers(engine, lang, header):
    plain = engine.get_analysis_prompt("[0.0 - 5.0] hi", 2, 3, prompt_language=lang)
    marked = engine.get_analysis_prompt("[0.0 - 5.0] hi [AUDIENCE: replayed 90%]", 2, 3, prompt_language=lang)
    assert header not in plain and header in marked


def test_english_prompt_asks_for_breakdown(engine):
    prompt = engine.get_analysis_prompt("[0.0 - 5.0] hi", 2, 3)
    assert '"score_breakdown": { "hook": 18' in prompt
    assert "viral_score MUST equal the sum" in prompt
    assert engine._wants_breakdown(None) and not engine._wants_breakdown(SimpleNamespace(prompt_language="id"))


def test_runner_collects_signals_and_survives_comment_failure(monkeypatch, tmp_path):
    from clipping import runner

    def boom(*a, **k):
        raise RuntimeError("HTTP Error 429")

    monkeypatch.setattr(scoring, "fetch_comments", boom)
    cfg = SimpleNamespace(audience_signals=True, use_comments=True, max_comments=50,
                          url_youtube="u", outputs_dir=str(tmp_path))
    s = runner._collect_audience_signals(cfg, INFO, "youtube")
    assert len(s["heatmap"]) == 4 and s["comment_marks"] == []
    assert json.loads((tmp_path / "audience_signals.json").read_text(encoding="utf-8"))["chapters"]
    assert runner._collect_audience_signals(cfg, INFO, "tiktok") is None
    cfg.audience_signals = False
    assert runner._collect_audience_signals(cfg, INFO, "youtube") is None


def test_cli_scoring_flags(tmp_path, monkeypatch):
    from clipping.config import build_config

    monkeypatch.chdir(tmp_path)
    cfg = build_config(["--url", "https://youtube.com/watch?v=x"])
    assert cfg.audience_signals and not cfg.use_comments and cfg.ai_temperature == 0.3
    assert cfg.score_weights == scoring.DEFAULT_WEIGHTS
    cfg = build_config(["--url", "https://youtube.com/watch?v=x", "--no-audience-signals", "--use-comments",
                        "--max-comments", "50", "--score-weights", "heatmap=0.5", "--ai-temperature", "0"])
    assert (cfg.audience_signals, cfg.use_comments, cfg.max_comments, cfg.ai_temperature) == (False, True, 50, 0.0)
    assert cfg.score_weights["heatmap"] == 0.5
    for bad in (["--score-weights", "audio=1"], ["--max-comments", "0"], ["--ai-temperature", "3"]):
        with pytest.raises(SystemExit):
            build_config(["--url", "https://youtube.com/watch?v=x", *bad])
