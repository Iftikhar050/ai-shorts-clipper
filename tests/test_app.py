"""Local web app: option validation, command building, job lifecycle, API, safety."""

import io
import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import jobs as J
from app import server as S


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Point every app path at a temp folder."""
    root = tmp_path / "repo"
    (root / "outputs").mkdir(parents=True)
    monkeypatch.setattr(J, "DATA_DIR", str(root / "app" / "data"))
    monkeypatch.setattr(J, "OUTPUTS_DIR", str(root / "outputs"))
    monkeypatch.setattr(S, "UPLOADS_DIR", str(root / "app" / "data" / "uploads"))
    monkeypatch.setattr(S, "SETTINGS_FILE", str(root / "app" / "data" / "settings.json"))
    monkeypatch.setattr(S, "ENV_FILE", str(root / ".env"))
    monkeypatch.setattr(S, "ACCOUNTS_FILE", str(root / "accounts.json"))
    monkeypatch.setattr(S, "ACCOUNTS_EXAMPLE", str(Path(J.ROOT) / "accounts.example.json"))
    return root


@pytest.fixture
def client(sandbox):
    store = J.JobStore(J.DATA_DIR)
    runner = J.JobRunner(store, settings_loader=S.load_settings)
    app = S.create_app(store, runner, start_runner=False)
    return TestClient(app), store, runner


# ------------------------------------------------------------- validation

def test_validate_options_defaults_and_bounds():
    o = J.validate_options({})
    assert (o["clips"], o["clip_length"], o["ratio"], o["loudnorm"], o["broll"]) == (3, "medium", "9:16", True, False)
    for bad in ({"clips": 0}, {"clips": 11}, {"ratio": "2:1"}, {"topic": "x" * 201}, {"topic": "a\nb"},
                {"time_range": "5:00-1:00"}, {"nope": 1}, {"clips": "many"}):
        with pytest.raises(ValueError):
            J.validate_options(bad)


@pytest.mark.parametrize("src", ["", "  ", "--help", "ftp://x", "not a link", "https://a\nb"])
def test_validate_source_rejects(src):
    with pytest.raises(ValueError):
        J.validate_source(src)


def test_build_command_maps_options_to_cli_flags(tmp_path):
    o = J.validate_options({"clips": 4, "topic": "money; rm -rf /", "time_range": "1:00-2:00", "visual_rerank": True,
                            "karaoke": False, "hook": False, "broll": True, "loudnorm": False})
    cmd = J.build_command("https://youtu.be/abcdefghijk", o, str(tmp_path), {"whisper_model": "base"})
    assert cmd[0] == J.pipeline_python() and cmd[2].endswith("main.py")
    if (Path(J.ROOT) / ".venv").is_dir():
        assert ".venv" in cmd[0]  # jobs never run the pipeline with a global Python
    pairs = dict(zip(cmd[3::1], cmd[4::1]))
    assert pairs["--url"] == "https://youtu.be/abcdefghijk" and pairs["--output-dir"] == str(tmp_path)
    assert pairs["--clips"] == "4" and pairs["--topic"] == "money; rm -rf /"   # one argv item, no shell
    assert pairs["--whisper-model"] == "base" and pairs["--gemini-model"] == "gemini-3.5-flash"
    for flag in ("--visual-rerank", "--no-karaoke", "--no-hook", "--no-loudnorm", "--no-bgm", "--use-dlp-subs"):
        assert flag in cmd
    assert "--no-broll" not in cmd and "--use-comments" not in cmd
    # the real CLI accepts every flag we build
    from clipping.config import _build_parser

    _build_parser().parse_args(cmd[3:])


def test_progress_percent():
    assert J.overall_percent("transcribe") == 10
    assert J.overall_percent("render", clip=1, total=3, frame_pct=0) == 60
    assert J.overall_percent("render", clip=2, total=3, frame_pct=50) == 79
    assert J.overall_percent("done") == 100


# ---------------------------------------------------------- job lifecycle

class FakeProc:
    def __init__(self, lines, code=0, on_run=None):
        self.stdout = iter(lines)
        self.code, self.on_run, self.pid = code, on_run, 4242

    def wait(self):
        if self.on_run:
            self.on_run()
        return self.code


def _write_outputs(folder, n_ok=2, n_fail=0):
    os.makedirs(folder, exist_ok=True)
    meta, manifest = [], []
    for r in range(1, n_ok + n_fail + 1):
        vid = os.path.join(folder, f"highlight_rank_{r}_ready.mp4")
        ok = r <= n_ok
        if ok:
            Path(vid).write_bytes(b"\x00" * 10)
            Path(folder, f"thumbnail_rank_{r}.jpg").write_bytes(b"jpg")
        meta.append({"rank": r, "title_inggris": f"Title {r}", "viral_score": 90 - r, "start_time": 10.0 * r,
                     "end_time": 10.0 * r + 40, "hook": {"text": f"hook {r}"}, "alasan": "because",
                     "score_breakdown": {"hook": 18, "emotion": 17, "shareability": 18, "clarity": 18, "payoff": 18},
                     "klasifikasi_akun": {"tipe_akun": "Science", "akun_tujuan": "@sci"},
                     "youtube_description_final": "desc", "hastag": "#a #b", "tiktok_caption_final": "cap"})
        manifest.append({"rank": r, "status": "success" if ok else "failed", "video_path": vid,
                         "thumbnail_path": os.path.join(folder, f"thumbnail_rank_{r}.jpg"), "duration": 40.0,
                         **({} if ok else {"error": "ffmpeg exploded"})})
    Path(folder, "metadata_preview.json").write_text(json.dumps(meta), encoding="utf-8")
    Path(folder, "render_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _run_one(runner, store, job, lines, code=0, outputs=(2, 0)):
    runner.popen = lambda *a, **k: FakeProc(lines, code, on_run=lambda: _write_outputs(job["output_dir"], *outputs))
    runner.run_job(job["id"])
    return store.get(job["id"])


def test_job_runs_tracks_progress_and_succeeds(client):
    c, store, runner = client
    job = runner.submit("https://youtu.be/abcdefghijk", J.validate_options({}))
    lines = ["@@PROGRESS {\"stage\": \"download\"}\n", "[1/3] downloading\n",
             "@@PROGRESS {\"stage\": \"render\", \"clip\": 2, \"total\": 2}\n", "Render frame:  50%\n",
             "@@PROGRESS {\"stage\": \"done\"}\n"]
    done = _run_one(runner, store, job, lines)
    assert (done["status"], done["rendered"], done["failed"], done["percent"]) == ("success", 2, 0, 100)
    log = c.get(f"/api/jobs/{job['id']}/log").json()
    assert "[1/3] downloading" in log["text"] and "@@PROGRESS" not in log["text"]
    clips = c.get(f"/api/jobs/{job['id']}/clips").json()["clips"]
    assert [x["rank"] for x in clips] == [1, 2] and clips[0]["video_url"].endswith("highlight_rank_1_ready.mp4")
    assert clips[0]["account"] == "Science" and clips[0]["score_breakdown"]["hook"] == 18
    r = c.get(clips[0]["video_url"])
    assert r.status_code == 200 and r.content == b"\x00" * 10


def test_partial_and_failed_jobs_are_reported_honestly(client):
    c, store, runner = client
    j1 = runner.submit("https://youtu.be/abcdefghijk", J.validate_options({}))
    assert _run_one(runner, store, j1, ["x\n"], code=1, outputs=(1, 1))["status"] == "partial"
    j2 = runner.submit("https://youtu.be/abcdefghijk", J.validate_options({}))
    runner.popen = lambda *a, **k: FakeProc(["working\n", "❌ ERROR: Gemini key missing\n", "Traceback...\n"], 1)
    runner.run_job(j2["id"])
    failed = store.get(j2["id"])
    assert failed["status"] == "failed" and "Gemini key missing" in failed["error"]


def test_cancel_and_restart_recovery(client, sandbox):
    c, store, runner = client
    queued = runner.submit("https://youtu.be/abcdefghijk", J.validate_options({}))
    assert runner.cancel(queued["id"]) and store.get(queued["id"])["status"] == "cancelled"
    assert not runner.cancel(queued["id"])
    store.update(queued["id"], status="running")
    again = runner.submit("https://youtu.be/abcdefghijk", J.validate_options({}))
    fresh = J.JobRunner(J.JobStore(J.DATA_DIR))
    fresh.popen = lambda *a, **k: FakeProc([], 0)
    fresh._loop = lambda: None  # don't start a real thread loop in tests
    fresh.start()
    assert fresh.store.get(queued["id"])["status"] == "failed"
    assert list(fresh.queue.queue) == [again["id"]]


# --------------------------------------------------------------------- API

def test_create_job_api_validation_and_queue(client):
    c, store, runner = client
    r = c.post("/api/jobs", json={"source": "https://youtu.be/abcdefghijk", "options": {"clips": 2}})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued" and body["queue_position"] == 1 and body["youtube_id"] == "abcdefghijk"
    assert "output_dir" not in body
    assert c.post("/api/jobs", json={"source": "--rm", "options": {}}).status_code == 422
    assert c.post("/api/jobs", json={"source": "https://x.y", "options": {"clips": 99}}).json()["detail"].startswith("clips")
    assert c.post("/api/jobs", content=b"[]", headers={"content-type": "application/json"}).status_code == 400
    assert c.get("/api/jobs/does-not-exist").status_code == 404


def test_upload_then_job_uses_local_file(client):
    c, store, runner = client
    r = c.post("/api/uploads", files={"file": ("talk.mp4", io.BytesIO(b"vid"), "video/mp4")})
    up = r.json()
    assert r.status_code == 200 and up["size"] == 3 and up["upload_id"].endswith(".mp4")
    assert c.post("/api/uploads", files={"file": ("evil.exe", io.BytesIO(b"x"), "application/x")}).status_code == 422
    job = c.post("/api/jobs", json={"upload_id": up["upload_id"], "title": "talk.mp4"}).json()
    assert job["is_upload"] and job["source"] == up["upload_id"] and job["title"] == "talk.mp4"
    assert os.path.isfile(store.get(job["id"])["source"])
    assert c.post("/api/jobs", json={"upload_id": "../../etc/passwd"}).status_code == 422


def test_media_route_blocks_traversal_and_unknown_types(client, sandbox):
    c, store, runner = client
    job = runner.submit("https://youtu.be/abcdefghijk", J.validate_options({}))
    _write_outputs(job["output_dir"])
    Path(job["output_dir"], "secret.txt").write_text("x")
    assert c.get(f"/media/{job['id']}/highlight_rank_1_ready.mp4").status_code == 200
    for name in ("secret.txt", "..%2F..%2F.env", "video_asli.part"):
        assert c.get(f"/media/{job['id']}/{name}").status_code == 404
    assert c.get("/media/cli-..%2Fx/a.mp4").status_code == 404


def test_cli_runs_are_listed_read_only(client, sandbox):
    c, store, runner = client
    _write_outputs(str(sandbox / "outputs" / "xeqTobW27yk_run1"), 3, 0)
    jobs = c.get("/api/jobs").json()["jobs"]
    cli = next(j for j in jobs if j["id"] == "cli-xeqTobW27yk_run1")
    assert cli["readonly"] and cli["rendered"] == 3 and cli["youtube_id"] == "xeqTobW27yk"
    assert len(c.get("/api/jobs/cli-xeqTobW27yk_run1/clips").json()["clips"]) == 3


def test_moved_and_unfinished_cli_runs(client, sandbox):
    c, *_ = client
    moved = sandbox / "outputs" / "abcdefghijk_run1"
    _write_outputs(str(moved), 2, 0)
    manifest = json.loads((moved / "render_manifest.json").read_text(encoding="utf-8"))
    for row in manifest:  # paths recorded before the folder was moved
        row["video_path"] = str(sandbox / "outputs" / os.path.basename(row["video_path"]))
    (moved / "render_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    running = sandbox / "outputs" / "zyxwvutsrqp"
    running.mkdir()
    (running / "metadata_preview.json").write_text(json.dumps([{"rank": 1, "title_inggris": "T"}]), encoding="utf-8")
    jobs = {j["id"]: j for j in c.get("/api/jobs").json()["jobs"]}
    assert jobs["cli-abcdefghijk_run1"]["status"] == "success" and jobs["cli-abcdefghijk_run1"]["rendered"] == 2
    assert jobs["cli-zyxwvutsrqp"]["status"] == "incomplete"


def test_settings_keys_are_written_but_never_returned(client, sandbox):
    c, store, runner = client
    (sandbox / ".env").write_text("# comment\nGOOGLE_API_KEY=your-gemini-api-key-here\nOTHER=keep\n", encoding="utf-8")
    s = c.get("/api/settings").json()
    assert s["keys"]["GOOGLE_API_KEY"] == {"set": False, "hint": None}
    r = c.put("/api/settings", json={"keys": {"GOOGLE_API_KEY": "AIzaRealKey12345"}, "settings": {"whisper_model": "base"}})
    assert r.status_code == 200
    body = r.json()
    assert body["keys"]["GOOGLE_API_KEY"] == {"set": True, "hint": "…2345"} and "AIzaRealKey12345" not in r.text
    env = (sandbox / ".env").read_text(encoding="utf-8")
    assert "GOOGLE_API_KEY=AIzaRealKey12345" in env and "OTHER=keep" in env and "# comment" in env
    assert body["settings"]["whisper_model"] == "base"
    for bad in ({"keys": {"HF_TOKEN": "x"}}, {"keys": {"GOOGLE_API_KEY": "bad key\nX=1"}},
                {"settings": {"whisper_device": "tpu"}}, {"settings": {"evil": "x"}}):
        assert c.put("/api/settings", json=bad).status_code == 422


def test_accounts_roundtrip_validates(client, sandbox):
    c, store, runner = client
    assert c.get("/api/accounts").json()["configured"] is False
    ok = {"accounts": {"Sports": {"handle": "@s", "angle_desc": "Match highlights"}}, "rules": ["r1"]}
    r = c.put("/api/accounts", json=ok)
    assert r.status_code == 200 and r.json()["configured"] and (sandbox / "accounts.json").is_file()
    bad = c.put("/api/accounts", json={"accounts": {"X": {"handle": "@x"}}})
    assert bad.status_code == 422 and "angle_desc" in bad.json()["detail"]
    assert json.loads((sandbox / "accounts.json").read_text(encoding="utf-8")) == ok
    assert c.delete("/api/accounts").json()["configured"] is False


def test_delete_only_removes_the_jobs_own_folder(client, sandbox):
    c, store, runner = client
    job = runner.submit("https://youtu.be/abcdefghijk", J.validate_options({}))
    assert c.delete(f"/api/jobs/{job['id']}").status_code == 409  # still queued
    runner.cancel(job["id"])
    _write_outputs(job["output_dir"])
    assert c.delete(f"/api/jobs/{job['id']}?files=true").status_code == 200
    assert not os.path.exists(job["output_dir"]) and (sandbox / "outputs").is_dir()


def test_index_and_static_served(client):
    c, *_ = client
    assert "Shorts Clipper" in c.get("/").text
    import re

    js = c.get("/static/app.js").text
    assert not re.search(r"\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML|document\.write", js)
