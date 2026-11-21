"""
clipping.performance — Learn from how your uploaded clips actually perform.

Loop:
  1. ingest   store each rendered clip's predicted scores (metadata_preview.json)
              and its YouTube video ID (render_manifest_uploaded.json, or `link`)
  2. fetch    snapshot real performance: views/likes/comments (YouTube Data API)
              + average view duration / percentage (YouTube Analytics API)
  3. report   rank-correlate every predictor (AI score, breakdown, signal
              components) with real outcomes; suggest --score-weights
  4. examples write your top performers to clipping/prompts/top_examples.json;
              the English prompt then shows them to the AI as references

Run via run_performance.py. Data lives in performance.sqlite3 (git-ignored).
Results need ~20-30 uploads before correlations mean much; the report says so.
"""

import json
import math
import os
import sqlite3
from datetime import datetime, timezone

DB_FILE = "performance.sqlite3"
TOKEN_FILE = os.path.join(".credentials", "youtube_analytics_token.json")
CLIENT_SECRET_FILE = os.path.join(".credentials", "client_secret.json")
SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
EXAMPLES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts", "top_examples.json")
MIN_FOR_CORRELATION = 5
RELIABLE_SAMPLE = 20
BREAKDOWN_KEYS = ("hook", "emotion", "shareability", "clarity", "payoff")
COMPONENT_KEYS = ("ai", "heatmap", "comments", "audio", "visual")
OUTCOMES = ("views", "avg_view_pct")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_dir TEXT NOT NULL,
  rank INTEGER NOT NULL,
  source_url TEXT,
  title TEXT,
  hook_text TEXT,
  start_time REAL,
  end_time REAL,
  account TEXT,
  ai_viral_score REAL,
  viral_score REAL,
  breakdown_json TEXT,
  components_json TEXT,
  visual_score REAL,
  youtube_video_id TEXT,
  published_at TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (run_dir, rank)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_clips_video ON clips(youtube_video_id) WHERE youtube_video_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  clip_id INTEGER NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
  captured_at TEXT NOT NULL,
  age_hours REAL,
  views INTEGER,
  likes INTEGER,
  comments INTEGER,
  avg_view_duration REAL,
  avg_view_pct REAL,
  shares INTEGER,
  subscribers_gained INTEGER
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: str = DB_FILE) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(_SCHEMA)
    return conn


# ---------------------------------------------------------------------------
# 1. Ingest
# ---------------------------------------------------------------------------

def _load_json(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def ingest_run(conn: sqlite3.Connection, run_dir: str) -> dict:
    """Store predictions for every clip in a run folder; link uploaded video IDs."""
    meta_path = os.path.join(run_dir, "metadata_preview.json")
    if not os.path.isfile(meta_path):
        raise FileNotFoundError(f"{meta_path} not found (run the clipper first).")
    clips = _load_json(meta_path)
    uploaded = {}
    for name in ("render_manifest.json", "render_manifest_uploaded.json"):  # uploaded wins
        up_path = os.path.join(run_dir, name)
        if os.path.isfile(up_path):
            data = _load_json(up_path)
            for row in data if isinstance(data, list) else data.get("items", []):
                if isinstance(row, dict) and row.get("youtube_video_id"):
                    uploaded[int(row.get("rank", 0))] = row["youtube_video_id"]

    run_key = os.path.abspath(run_dir)
    added = linked = 0
    for c in clips:
        rank = int(c.get("rank", 0))
        hook = c.get("hook") if isinstance(c.get("hook"), dict) else {}
        values = {
            "run_dir": run_key, "rank": rank, "source_url": c.get("source_url"),
            "title": c.get("title_inggris") or c.get("title_indonesia"),
            "hook_text": hook.get("text") or "",
            "start_time": c.get("start_time"), "end_time": c.get("end_time"),
            "account": (c.get("klasifikasi_akun") or {}).get("tipe_akun"),
            "ai_viral_score": c.get("ai_viral_score", c.get("viral_score")),
            "viral_score": c.get("viral_score"),
            "breakdown_json": json.dumps(c.get("score_breakdown")) if c.get("score_breakdown") else None,
            "components_json": json.dumps(c.get("score_components")) if c.get("score_components") else None,
            "visual_score": c.get("visual_score"),
            "created_at": _now(),
        }
        cur = conn.execute(
            f"INSERT OR IGNORE INTO clips ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
            list(values.values()),
        )
        added += cur.rowcount
        if rank in uploaded:
            linked += link_clip(conn, run_dir, rank, uploaded[rank])
    conn.commit()
    return {"clips": len(clips), "added": added, "linked": linked}


def link_clip(conn: sqlite3.Connection, run_dir: str, rank: int, video_id: str) -> int:
    """Attach a YouTube video ID to a stored clip (for manual uploads)."""
    cur = conn.execute(
        "UPDATE clips SET youtube_video_id = ? WHERE run_dir = ? AND rank = ?",
        (video_id.strip(), os.path.abspath(run_dir), int(rank)),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise LookupError(f"No stored clip rank {rank} for {run_dir}; run `ingest` first.")
    return cur.rowcount


# ---------------------------------------------------------------------------
# 2. Fetch (Google APIs; services are injectable for tests)
# ---------------------------------------------------------------------------

def authorize(client_secret: str = CLIENT_SECRET_FILE, token_file: str = TOKEN_FILE) -> str:
    """One-time browser sign-in; saves a read-only token next to the uploader's."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not os.path.isfile(client_secret):
        raise FileNotFoundError(
            f"{client_secret} not found. Create an OAuth client (Desktop app) in Google Cloud, "
            "enable 'YouTube Data API v3' and 'YouTube Analytics API', download the JSON and save it there."
        )
    flow = InstalledAppFlow.from_client_secrets_file(client_secret, scopes=SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    os.makedirs(os.path.dirname(token_file), exist_ok=True)
    with open(token_file, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())
    return token_file


def build_services(token_file: str = TOKEN_FILE):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not os.path.isfile(token_file):
        raise FileNotFoundError(f"{token_file} not found; run `python run_performance.py auth` first.")
    creds = Credentials.from_authorized_user_file(token_file, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_file, "w", encoding="utf-8") as fh:
            fh.write(creds.to_json())
    return build("youtube", "v3", credentials=creds), build("youtubeAnalytics", "v2", credentials=creds)


def _age_hours(published_at: str | None, now: datetime) -> float | None:
    if not published_at:
        return None
    pub = datetime.strptime(published_at[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return round((now - pub).total_seconds() / 3600, 1)


def fetch_metrics(conn: sqlite3.Connection, youtube, analytics, now: datetime | None = None) -> int:
    """Snapshot stats for every linked clip. Returns snapshots written."""
    now = now or datetime.now(timezone.utc)
    rows = conn.execute("SELECT id, youtube_video_id FROM clips WHERE youtube_video_id IS NOT NULL").fetchall()
    by_video = {r["youtube_video_id"]: r["id"] for r in rows}
    written = 0
    ids = list(by_video)
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        resp = youtube.videos().list(part="statistics,snippet", id=",".join(batch)).execute()
        for item in resp.get("items", []):
            vid, stats = item["id"], item.get("statistics", {})
            published = item.get("snippet", {}).get("publishedAt")
            conn.execute("UPDATE clips SET published_at = ? WHERE id = ?", (published, by_video[vid]))
            extra = _analytics_for(analytics, vid, published, now)
            conn.execute(
                "INSERT INTO metrics (clip_id, captured_at, age_hours, views, likes, comments, "
                "avg_view_duration, avg_view_pct, shares, subscribers_gained) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (by_video[vid], now.strftime("%Y-%m-%dT%H:%M:%SZ"), _age_hours(published, now),
                 int(stats.get("viewCount", 0)), int(stats.get("likeCount", 0)),
                 int(stats.get("commentCount", 0)), extra.get("averageViewDuration"),
                 extra.get("averageViewPercentage"), extra.get("shares"), extra.get("subscribersGained")),
            )
            written += 1
    conn.commit()
    return written


def _analytics_for(analytics, video_id: str, published: str | None, now: datetime) -> dict:
    """Retention metrics from YouTube Analytics (own channel only); {} if unavailable."""
    if analytics is None or not published:
        return {}
    try:
        resp = analytics.reports().query(
            ids="channel==MINE", startDate=published[:10], endDate=now.strftime("%Y-%m-%d"),
            metrics="averageViewDuration,averageViewPercentage,shares,subscribersGained",
            filters=f"video=={video_id}",
        ).execute()
    except Exception as exc:  # noqa: BLE001 - e.g. analytics not ready for brand-new uploads
        print(f"   ⚠️ Analytics unavailable for {video_id}: {exc}")
        return {}
    rows = resp.get("rows") or []
    if not rows:
        return {}
    names = [h["name"] for h in resp.get("columnHeaders", [])]
    return dict(zip(names, rows[0]))


# ---------------------------------------------------------------------------
# 3. Report
# ---------------------------------------------------------------------------

def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation (ties averaged); None if undefined."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    if vx == 0 or vy == 0:
        return None
    return round(cov / (vx * vy), 3)


def performance_rows(conn: sqlite3.Connection, min_age_hours: float = 0) -> list[dict]:
    """One row per linked clip: predictions + its latest snapshot at >= min_age_hours."""
    rows = conn.execute(
        """SELECT c.*, m.views, m.likes, m.comments, m.avg_view_pct, m.avg_view_duration, m.age_hours
           FROM clips c JOIN metrics m ON m.clip_id = c.id
           WHERE m.id = (SELECT m2.id FROM metrics m2 WHERE m2.clip_id = c.id
                         AND COALESCE(m2.age_hours, 0) >= ? ORDER BY m2.captured_at DESC LIMIT 1)""",
        (min_age_hours,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["breakdown"] = json.loads(d.pop("breakdown_json") or "null") or {}
        d["components"] = json.loads(d.pop("components_json") or "null") or {}
        out.append(d)
    return out


def predictors(row: dict) -> dict:
    p = {"ai_viral_score": row.get("ai_viral_score"), "final_viral_score": row.get("viral_score")}
    p.update({f"breakdown.{k}": row["breakdown"].get(k) for k in BREAKDOWN_KEYS})
    p.update({f"component.{k}": row["components"].get(k) for k in COMPONENT_KEYS})
    return p


def correlations(rows: list[dict], outcome: str) -> dict:
    """{predictor: (spearman, n)} for every predictor with enough data."""
    result = {}
    names = predictors(rows[0]).keys() if rows else []
    for name in names:
        pairs = [(predictors(r)[name], r.get(outcome)) for r in rows]
        pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
        if len(pairs) >= MIN_FOR_CORRELATION:
            rho = spearman([x for x, _ in pairs], [y for _, y in pairs])
            if rho is not None:
                result[name] = (rho, len(pairs))
    return result


def suggest_weights(corrs: dict) -> str | None:
    """Weights proportional to each component's positive correlation (ai floored at 0.1)."""
    comp = {k.split(".", 1)[1]: rho for k, (rho, _) in corrs.items() if k.startswith("component.")}
    if "ai" not in comp:
        return None
    raw = {k: max(0.0, v) for k, v in comp.items()}
    raw["ai"] = max(raw["ai"], 0.1)
    total = sum(raw.values())
    return ",".join(f"{k}={v / total:.2f}" for k, v in raw.items())


def build_report(conn: sqlite3.Connection, min_age_hours: float = 72) -> str:
    rows = performance_rows(conn, min_age_hours)
    linked = conn.execute("SELECT COUNT(*) FROM clips WHERE youtube_video_id IS NOT NULL").fetchone()[0]
    lines = [f"Clips with performance data (>= {min_age_hours:.0f}h old): {len(rows)}  |  linked uploads: {linked}"]
    if len(rows) < MIN_FOR_CORRELATION:
        lines.append(f"Need at least {MIN_FOR_CORRELATION} (ideally {RELIABLE_SAMPLE}+) to correlate. Keep posting.")
        return "\n".join(lines)
    if len(rows) < RELIABLE_SAMPLE:
        lines.append(f"⚠️ Only {len(rows)} clips: treat correlations as early hints, not conclusions.")
    for outcome in OUTCOMES:
        corrs = correlations(rows, outcome)
        if not corrs:
            continue
        lines.append(f"\nWhich predictions track real {outcome}?  (Spearman: +1 perfect, 0 none, -1 inverse)")
        for name, (rho, n) in sorted(corrs.items(), key=lambda kv: -kv[1][0]):
            bar = "#" * int(round(abs(rho) * 20))
            lines.append(f"  {name:28} {rho:+.2f}  n={n:<3} {'+' if rho >= 0 else '-'}{bar}")
        if outcome == "views":
            w = suggest_weights(corrs)
            if w:
                lines.append(f"  Suggested --score-weights (from views): {w}")
    top = sorted(rows, key=lambda r: r.get("views") or 0, reverse=True)[:3]
    lines.append("\nTop performers:")
    for r in top:
        lines.append(f"  {r.get('views') or 0:>8} views | pred {r.get('viral_score')} | {r.get('title')}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. Few-shot examples for the prompt
# ---------------------------------------------------------------------------

def write_examples(conn: sqlite3.Connection, path: str | None = None, top: int = 3,
                   min_age_hours: float = 72) -> list[dict]:
    path = path or EXAMPLES_FILE
    rows = performance_rows(conn, min_age_hours)
    best = sorted(rows, key=lambda r: r.get("views") or 0, reverse=True)[:top]
    examples = [{
        "title": r.get("title"), "hook": r.get("hook_text"), "views": r.get("views"),
        "avg_view_pct": r.get("avg_view_pct"),
        "duration_s": round((r.get("end_time") or 0) - (r.get("start_time") or 0), 1),
        "account": r.get("account"),
    } for r in best]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(examples, fh, ensure_ascii=False, indent=2)
    return examples


def load_examples_section(path: str | None = None) -> str:
    """Prompt section listing your proven clips ('' when there are none)."""
    try:
        examples = _load_json(path or EXAMPLES_FILE)
    except (OSError, ValueError):
        return ""
    if not isinstance(examples, list) or not examples:
        return ""
    lines = ["\n\nYOUR PROVEN CLIPS (REAL RESULTS ON THIS CHANNEL):",
             "These past clips performed best with this audience. Use them as a reference for which hooks, "
             "angles and lengths work here; do not copy them."]
    for e in examples:
        pct = f", {e['avg_view_pct']:.0f}% average viewed" if e.get("avg_view_pct") is not None else ""
        lines.append(f"- \"{e.get('title')}\" | hook: \"{e.get('hook')}\" | {e.get('duration_s')}s | "
                     f"{e.get('views')} views{pct}")
    return "\n".join(lines) + "\n"
