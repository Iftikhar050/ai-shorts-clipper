"""Performance tracking: ingest, link, fetch (fake Google services), report, examples."""

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from clipping import performance as perf

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 12, 10, 12, 0, tzinfo=timezone.utc)


def _clip(rank, score, ai, hook=15, **extra):
    return {"rank": rank, "viral_score": score, "ai_viral_score": ai, "start_time": 10.0 * rank,
            "end_time": 10.0 * rank + 30, "title_inggris": f"Clip {rank}", "hook": {"text": f"hook {rank}"},
            "klasifikasi_akun": {"tipe_akun": "Science"},
            "score_breakdown": {"hook": hook, "emotion": 15, "shareability": 15, "clarity": 15, "payoff": 15},
            "score_components": {"ai": ai / 100, "visual": extra.pop("visual", 0.5)}, **extra}


def _run_dir(tmp_path, name, clips, uploaded=None):
    d = tmp_path / name
    d.mkdir()
    (d / "metadata_preview.json").write_text(json.dumps(clips), encoding="utf-8")
    if uploaded:
        (d / "render_manifest_uploaded.json").write_text(
            json.dumps([{"rank": r, "youtube_video_id": v, "youtube_upload_status": "uploaded"}
                        for r, v in uploaded.items()]), encoding="utf-8")
    return d


class FakeYouTube:
    def __init__(self, stats):
        self.stats, self.calls = stats, []

    def videos(self):
        return self

    def list(self, part, id):
        self.calls.append(id.split(","))
        items = [{"id": v, "statistics": {"viewCount": str(s[0]), "likeCount": "5", "commentCount": "1"},
                  "snippet": {"publishedAt": "2026-12-01T12:00:00Z"}} for v, s in self.stats.items()
                 if v in id.split(",")]
        return SimpleNamespace(execute=lambda: {"items": items})


class FakeAnalytics:
    def __init__(self, pct, fail=()):
        self.pct, self.fail = pct, set(fail)

    def reports(self):
        return self

    def query(self, **kw):
        vid = kw["filters"].split("==")[1]

        def execute():
            if vid in self.fail:
                raise RuntimeError("analytics not ready")
            return {"columnHeaders": [{"name": n} for n in
                                      ("averageViewDuration", "averageViewPercentage", "shares", "subscribersGained")],
                    "rows": [[20.5, self.pct[vid], 3, 1]]}
        return SimpleNamespace(execute=execute)


def test_ingest_links_uploads_and_is_idempotent(tmp_path):
    conn = perf.connect(str(tmp_path / "p.sqlite3"))
    run = _run_dir(tmp_path, "run1", [_clip(1, 90, 88), _clip(2, 80, 85)], uploaded={1: "vidA"})
    assert perf.ingest_run(conn, str(run)) == {"clips": 2, "added": 2, "linked": 1}
    assert perf.ingest_run(conn, str(run))["added"] == 0
    row = conn.execute("SELECT * FROM clips WHERE rank = 1").fetchone()
    assert (row["youtube_video_id"], row["title"], row["hook_text"], row["account"]) == ("vidA", "Clip 1", "hook 1", "Science")
    assert json.loads(row["breakdown_json"])["hook"] == 15
    perf.link_clip(conn, str(run), 2, " vidB ")
    assert conn.execute("SELECT youtube_video_id FROM clips WHERE rank = 2").fetchone()[0] == "vidB"
    with pytest.raises(LookupError):
        perf.link_clip(conn, str(run), 9, "x")
    with pytest.raises(FileNotFoundError):
        perf.ingest_run(conn, str(tmp_path / "missing"))


def test_fetch_metrics_snapshots_and_tolerates_analytics_gaps(tmp_path):
    conn = perf.connect(str(tmp_path / "p.sqlite3"))
    run = _run_dir(tmp_path, "run1", [_clip(1, 90, 88), _clip(2, 80, 85)], uploaded={1: "vidA", 2: "vidB"})
    perf.ingest_run(conn, str(run))
    yt = FakeYouTube({"vidA": (1000,), "vidB": (50,)})
    n = perf.fetch_metrics(conn, yt, FakeAnalytics({"vidA": 71.0, "vidB": 30.0}, fail={"vidB"}), now=NOW)
    assert n == 2 and len(yt.calls) == 1
    m = {r["youtube_video_id"]: r for r in conn.execute(
        "SELECT c.youtube_video_id, m.* FROM metrics m JOIN clips c ON c.id = m.clip_id")}
    assert (m["vidA"]["views"], m["vidA"]["avg_view_pct"], m["vidA"]["age_hours"]) == (1000, 71.0, 216.0)
    assert m["vidB"]["views"] == 50 and m["vidB"]["avg_view_pct"] is None


@pytest.mark.parametrize("xs, ys, expected", [
    ([1, 2, 3, 4], [10, 20, 30, 40], 1.0), ([1, 2, 3, 4], [4, 3, 2, 1], -1.0),
    ([1, 1, 2, 3], [1, 2, 3, 4], 0.949), ([1, 1, 1], [1, 2, 3], None), ([1, 2], [1, 2], None),
])
def test_spearman(xs, ys, expected):
    assert perf.spearman(xs, ys) == expected


def _populated(tmp_path, n):
    conn = perf.connect(str(tmp_path / "p.sqlite3"))
    # hook score and visual track views perfectly; AI score is reversed
    clips = [_clip(i, 50 + i, 100 - i, hook=i, visual=i / 10) for i in range(1, n + 1)]
    run = _run_dir(tmp_path, "run", clips, uploaded={i: f"v{i}" for i in range(1, n + 1)})
    perf.ingest_run(conn, str(run))
    perf.fetch_metrics(conn, FakeYouTube({f"v{i}": (i * 100,) for i in range(1, n + 1)}),
                       FakeAnalytics({f"v{i}": 30.0 + i for i in range(1, n + 1)}), now=NOW)
    return conn


def test_report_needs_enough_clips(tmp_path):
    out = perf.build_report(_populated(tmp_path, 3))
    assert "Clips with performance data (>= 72h old): 3" in out and "Need at least 5" in out


def test_report_correlations_and_weight_suggestion(tmp_path):
    conn = _populated(tmp_path, 6)
    rows = perf.performance_rows(conn, 72)
    corrs = perf.correlations(rows, "views")
    assert corrs["breakdown.hook"] == (1.0, 6) and corrs["ai_viral_score"] == (-1.0, 6)
    assert corrs["component.visual"][0] == 1.0
    assert perf.suggest_weights(corrs) == "ai=0.09,visual=0.91"
    out = perf.build_report(conn)
    assert "early hints" in out and "Suggested --score-weights (from views): ai=0.09,visual=0.91" in out
    assert "breakdown.hook" in out and "600 views" in out.splitlines()[-3]
    assert perf.build_report(conn, min_age_hours=10_000).count("Need at least") == 1


def test_examples_flow_into_the_english_prompt_only(tmp_path, monkeypatch):
    conn = _populated(tmp_path, 6)
    path = tmp_path / "top_examples.json"
    monkeypatch.setattr(perf, "EXAMPLES_FILE", str(path))
    ex = perf.write_examples(conn, top=2)
    assert [e["title"] for e in ex] == ["Clip 6", "Clip 5"] and ex[0]["duration_s"] == 30.0
    section = perf.load_examples_section()
    assert "YOUR PROVEN CLIPS" in section and '"Clip 6" | hook: "hook 6" | 30.0s | 600 views, 36% average viewed' in section

    for name, symbol in (("yt_dlp", "YoutubeDL"), ("faster_whisper", "WhisperModel")):
        stub = ModuleType(name)
        setattr(stub, symbol, object)
        monkeypatch.setitem(sys.modules, name, stub)
    spec = importlib.util.spec_from_file_location("_perf_engine", ROOT / "clipping" / "engine.py")
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    assert "YOUR PROVEN CLIPS" in engine.get_analysis_prompt("[0.0 - 5.0] hi", 1, 3)
    assert "YOUR PROVEN CLIPS" not in engine.get_analysis_prompt("[0.0 - 5.0] hi", 1, 3, prompt_language="id")
    assert "YOUR PROVEN CLIPS" not in engine.get_analysis_prompt(
        "[0.0 - 5.0] hi", 1, 3, cfg=SimpleNamespace(use_examples=False))
    path.write_text("not json", encoding="utf-8")
    assert perf.load_examples_section() == ""


def test_cli_commands(tmp_path, capsys):
    import run_performance

    db = str(tmp_path / "cli.sqlite3")
    run = _run_dir(tmp_path, "r", [_clip(1, 90, 88)])
    assert run_performance.main(["--db", db, "ingest", str(run)]) == 0
    assert run_performance.main(["--db", db, "link", str(run), "1", "vidZ"]) == 0
    assert run_performance.main(["--db", db, "report"]) == 0
    out = capsys.readouterr().out
    assert "1 clips (1 new), 0 linked" in out and "-> vidZ" in out and "Need at least 5" in out
