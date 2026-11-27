"""
app.jobs — Job queue for the web interface.

Every job runs the real CLI (`main.py`) in a subprocess, so the web app and the
command line always behave the same and get every pipeline feature. Jobs run
one at a time (rendering is CPU-heavy), their output goes to outputs/<job-id>/,
and progress comes from the pipeline's "@@PROGRESS {json}" lines.
"""

import json
import os
import queue
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "app", "data")
OUTPUTS_DIR = os.path.join(ROOT, "outputs")
PROGRESS_PREFIX = "@@PROGRESS "


def pipeline_python() -> str:
    """The project's .venv Python (has Whisper, OpenCV...); falls back to this interpreter."""
    for rel in (("Scripts", "python.exe"), ("bin", "python")):
        candidate = os.path.join(ROOT, ".venv", *rel)
        if os.path.isfile(candidate):
            return candidate
    return sys.executable

# Fixed machine settings (overridable in app/data/settings.json via the UI)
DEFAULT_SETTINGS = {
    "gemini_model": "gemini-3.5-flash",
    "gemini_fallback_model": "gemini-3.8-flash",
    "whisper_model": "small",
    "whisper_device": "cpu",
    "whisper_compute_type": "int8",
    "source_height": "1080",
}

OPTION_RULES = {
    "clips": ("int", 1, 10, 3),
    "clip_length": ("enum", ["short", "medium", "long", "any"], "medium"),
    "ratio": ("enum", ["9:16", "1:1", "4:5", "16:9"], "9:16"),
    "font_style": ("enum", ["DEFAULT", "STORYTELLER", "HORMOZI", "CINEMATIC"], "DEFAULT"),
    "words_per_sub": ("int", 1, 8, 5),
    "output_language": ("text", 40, "English"),
    "topic": ("text", 200, ""),
    "time_range": ("text", 40, ""),
    "visual_rerank": ("bool", False),
    "use_comments": ("bool", False),
    "karaoke": ("bool", True),
    "hook": ("bool", True),
    "broll": ("bool", False),
    "bgm": ("bool", False),
    "loudnorm": ("bool", True),
}

# Overall progress weight per stage: (start %, end %)
STAGE_SPAN = {
    "queued": (0, 0), "starting": (0, 2), "download": (2, 10), "transcribe": (10, 35),
    "signals": (35, 40), "analyze": (40, 55), "visual": (55, 60), "render": (60, 99), "done": (100, 100),
}
STAGE_LABELS = {
    "queued": "Queued", "starting": "Starting", "download": "Getting video", "transcribe": "Transcribing",
    "signals": "Reading signals", "analyze": "Finding best moments", "visual": "Reviewing visuals",
    "render": "Rendering clips", "done": "Finished",
}
_FRAME_RE = re.compile(r"Render frame:\s+(\d{1,3})%")
_LOG_TAIL_CHARS = 64_000


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_job_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)


def validate_options(raw: dict | None) -> dict:
    """Coerce UI options to safe, known values. Raises ValueError on bad input."""
    raw = raw or {}
    unknown = set(raw) - set(OPTION_RULES)
    if unknown:
        raise ValueError(f"Unknown option(s): {', '.join(sorted(unknown))}")
    out = {}
    for name, rule in OPTION_RULES.items():
        kind, default = rule[0], rule[-1]
        value = raw.get(name, default)
        if kind == "int":
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ValueError(f"{name} must be a whole number") from None
            if not rule[1] <= value <= rule[2]:
                raise ValueError(f"{name} must be between {rule[1]} and {rule[2]}")
        elif kind == "enum":
            if value not in rule[1]:
                raise ValueError(f"{name} must be one of {', '.join(rule[1])}")
        elif kind == "bool":
            value = bool(value)
        else:  # text
            value = str(value or "").strip()
            if len(value) > rule[1]:
                raise ValueError(f"{name} is too long (max {rule[1]} characters)")
            if any(ch in value for ch in "\r\n\x00"):
                raise ValueError(f"{name} must be a single line")
        out[name] = value
    if out["time_range"]:
        from clipping.selection import parse_time_range

        parse_time_range(out["time_range"])  # raises ValueError with a clear message
    if not out["output_language"]:
        out["output_language"] = "English"
    return out


def validate_source(source: str) -> str:
    source = (source or "").strip()
    if not source:
        raise ValueError("Paste a video link or upload a file.")
    if source.startswith("-") or any(ch in source for ch in "\r\n\x00"):
        raise ValueError("That doesn't look like a valid link.")
    if not (re.match(r"^https?://", source, re.I) or os.path.isfile(source)):
        raise ValueError("Use an http(s) link (YouTube, TikTok, Instagram, Google Drive) or upload a file.")
    return source


def build_command(source: str, options: dict, output_dir: str, settings: dict | None = None) -> list[str]:
    """argv for main.py. A list (no shell), so nothing in the options is interpreted."""
    s = {**DEFAULT_SETTINGS, **(settings or {})}
    cmd = [
        pipeline_python(), "-u", os.path.join(ROOT, "main.py"),
        "--url", source,
        "--output-dir", output_dir,
        "--clips", str(options["clips"]),
        "--clip-length", options["clip_length"],
        "--ratio", options["ratio"],
        "--font-style", options["font_style"],
        "--words-per-sub", str(options["words_per_sub"]),
        "--output-language", options["output_language"],
        "--gemini-model", s["gemini_model"],
        "--gemini-fallback-model", s["gemini_fallback_model"],
        "--whisper-model", s["whisper_model"],
        "--whisper-device", s["whisper_device"],
        "--whisper-compute-type", s["whisper_compute_type"],
        "--source-height", str(s["source_height"]),
        "--face-detector", "mediapipe",
        "--use-dlp-subs",
    ]
    if options["topic"]:
        cmd += ["--topic", options["topic"]]
    if options["time_range"]:
        cmd += ["--time-range", options["time_range"]]
    for flag, on in (("--visual-rerank", options["visual_rerank"]), ("--use-comments", options["use_comments"])):
        if on:
            cmd.append(flag)
    for flag, on in (("--no-karaoke", options["karaoke"]), ("--no-hook", options["hook"]),
                     ("--no-broll", options["broll"]), ("--no-bgm", options["bgm"]),
                     ("--no-loudnorm", options["loudnorm"])):
        if not on:
            cmd.append(flag)
    accounts = os.path.join(ROOT, "accounts.json")
    if os.path.isfile(accounts):
        cmd += ["--accounts-file", accounts]
    return cmd


def overall_percent(stage: str, clip: int = 0, total: int = 0, frame_pct: int = 0) -> int:
    lo, hi = STAGE_SPAN.get(stage, (0, 0))
    if stage == "render" and total:
        per = (hi - lo) / total
        return int(lo + per * (max(clip, 1) - 1) + per * min(frame_pct, 100) / 100)
    return lo


class JobStore:
    """Jobs persisted to app/data/jobs.json (thread-safe)."""

    def __init__(self, data_dir: str = DATA_DIR):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, "jobs.json")
        self.logs_dir = os.path.join(data_dir, "logs")
        os.makedirs(self.logs_dir, exist_ok=True)
        self._lock = threading.RLock()
        self._jobs = {}
        if os.path.isfile(self.path):
            try:
                with open(self.path, encoding="utf-8") as fh:
                    self._jobs = {j["id"]: j for j in json.load(fh)}
            except (OSError, ValueError, KeyError):
                self._jobs = {}

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(list(self._jobs.values()), fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def add(self, job: dict):
        with self._lock:
            self._jobs[job["id"]] = job
            self._save()

    def update(self, job_id: str, **fields):
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(fields)
                self._save()

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def all(self) -> list[dict]:
        with self._lock:
            return sorted((dict(j) for j in self._jobs.values()), key=lambda j: j["created_at"], reverse=True)

    def remove(self, job_id: str):
        with self._lock:
            self._jobs.pop(job_id, None)
            self._save()

    def log_path(self, job_id: str) -> str:
        return os.path.join(self.logs_dir, f"{job_id}.log")


class JobRunner:
    """Runs queued jobs one at a time in a background thread."""

    def __init__(self, store: JobStore, settings_loader=lambda: {}, popen=subprocess.Popen):
        self.store = store
        self.settings_loader = settings_loader
        self.popen = popen
        self.queue: "queue.Queue[str]" = queue.Queue()
        self.current = None  # (job_id, process)
        self._cancelled = set()
        self._thread = None

    def start(self):
        # Recover from a restart: running jobs were interrupted; queued ones run again.
        for job in reversed(self.store.all()):
            if job["status"] == "running":
                self.store.update(job["id"], status="failed", stage_label="Interrupted",
                                  error="The app was closed while this job was running.", finished_at=_now())
            elif job["status"] == "queued":
                self.queue.put(job["id"])
        self._thread = threading.Thread(target=self._loop, daemon=True, name="clip-jobs")
        self._thread.start()

    def submit(self, source: str, options: dict, title: str | None = None) -> dict:
        job_id = new_job_id()
        job = {
            "id": job_id, "source": source, "title": title or source, "options": options,
            "status": "queued", "stage": "queued", "stage_label": STAGE_LABELS["queued"], "percent": 0,
            "clip": 0, "total_clips": 0, "created_at": _now(), "started_at": None, "finished_at": None,
            "output_dir": os.path.join(OUTPUTS_DIR, job_id), "rendered": 0, "failed": 0, "error": None,
            "exit_code": None,
        }
        self.store.add(job)
        self.queue.put(job_id)
        return job

    def cancel(self, job_id: str) -> bool:
        job = self.store.get(job_id)
        if not job or job["status"] not in ("queued", "running"):
            return False
        self._cancelled.add(job_id)
        if job["status"] == "queued":
            self.store.update(job_id, status="cancelled", stage_label="Cancelled", finished_at=_now())
            return True
        if self.current and self.current[0] == job_id:
            _kill_tree(self.current[1])
        return True

    def queue_position(self, job_id: str) -> int | None:
        ids = [j for j in list(self.queue.queue)]
        return ids.index(job_id) + 1 if job_id in ids else None

    def _loop(self):
        while True:
            job_id = self.queue.get()
            try:
                if job_id in self._cancelled or (self.store.get(job_id) or {}).get("status") != "queued":
                    continue
                self.run_job(job_id)
            except Exception as exc:  # never let one job kill the worker
                self.store.update(job_id, status="failed", error=f"Internal error: {exc}", finished_at=_now())
            finally:
                self.queue.task_done()

    def run_job(self, job_id: str):
        job = self.store.get(job_id)
        os.makedirs(job["output_dir"], exist_ok=True)
        cmd = build_command(job["source"], job["options"], job["output_dir"], self.settings_loader())
        self.store.update(job_id, status="running", stage="starting", stage_label=STAGE_LABELS["starting"],
                          percent=1, started_at=_now())
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
        kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        with open(self.store.log_path(job_id), "w", encoding="utf-8") as log:
            proc = self.popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace", bufsize=1, **kwargs)
            self.current = (job_id, proc)
            state = {"stage": "starting", "clip": 0, "total": 0, "frame": 0}
            last_save = 0.0
            for line in proc.stdout:
                log.write(line)
                log.flush()
                changed = self._apply_line(line, state)
                if changed or time.time() - last_save > 2:
                    self._push_state(job_id, state)
                    last_save = time.time()
            code = proc.wait()
            self.current = None
        self._finish(job_id, code)

    @staticmethod
    def _apply_line(line: str, state: dict) -> bool:
        if line.startswith(PROGRESS_PREFIX):
            try:
                data = json.loads(line[len(PROGRESS_PREFIX):])
            except ValueError:
                return False
            state["stage"] = data.get("stage", state["stage"])
            if state["stage"] == "render":
                state["clip"], state["total"], state["frame"] = data.get("clip", 0), data.get("total", 0), 0
            return True
        m = _FRAME_RE.search(line)
        if m and state["stage"] == "render":
            state["frame"] = int(m.group(1))
        return False

    def _push_state(self, job_id: str, state: dict):
        label = STAGE_LABELS.get(state["stage"], state["stage"])
        if state["stage"] == "render" and state["total"]:
            label = f"Rendering clip {state['clip']} of {state['total']}"
        self.store.update(job_id, stage=state["stage"], stage_label=label, clip=state["clip"],
                          total_clips=state["total"],
                          percent=overall_percent(state["stage"], state["clip"], state["total"], state["frame"]))

    def _finish(self, job_id: str, code: int):
        job = self.store.get(job_id)
        from clipping.results import summarize_render

        manifest = _read_json(os.path.join(job["output_dir"], "render_manifest.json")) or []
        ok, failed, _ = summarize_render(manifest if isinstance(manifest, list) else [])
        if job_id in self._cancelled:
            status, label, error = "cancelled", "Cancelled", None
        elif code == 0 and ok:
            status, label, error = "success", "Finished", None
        elif ok:
            status, label, error = "partial", f"{ok} rendered, {failed} failed", None
        else:
            status, label = "failed", "Failed"
            error = _last_error(self.store.log_path(job_id)) or f"Pipeline exited with code {code}"
        self.store.update(job_id, status=status, stage="done" if status != "failed" else job["stage"],
                          stage_label=label, percent=100 if status in ("success", "partial") else job["percent"],
                          rendered=ok, failed=failed, error=error, exit_code=code, finished_at=_now())


def _kill_tree(proc):
    """Stop the pipeline and its FFmpeg children."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _last_error(log_path: str) -> str | None:
    """Most useful error line from the end of a log."""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read()[-_LOG_TAIL_CHARS:].splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        s = line.strip()
        if s and (s.startswith(("❌", "ERROR", "RuntimeError", "ValueError", "FileNotFoundError"))
                  or "Error:" in s or "error:" in s):
            return s[:400]
    tail = [l.strip() for l in lines if l.strip() and not l.startswith(PROGRESS_PREFIX)]
    return tail[-1][:400] if tail else None


def read_log(job_id: str, store: JobStore, offset: int = 0, limit: int = 200_000) -> tuple[str, int]:
    """Log text from *offset* (bytes) with progress lines removed; returns (text, next_offset)."""
    path = store.log_path(job_id)
    if not os.path.isfile(path):
        return "", 0
    with open(path, "rb") as fh:
        fh.seek(max(0, offset))
        data = fh.read(limit)
    text = data.decode("utf-8", errors="replace")
    text = "\n".join(l for l in text.split("\n") if not l.startswith(PROGRESS_PREFIX))
    return text, offset + len(data)
