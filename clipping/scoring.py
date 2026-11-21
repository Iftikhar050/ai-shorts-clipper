"""
clipping.scoring — Score breakdown and real audience signals.

Two layers on top of the AI's own opinion:

1. Score breakdown: the AI reports its five 1-20 component scores
   (hook, emotion, shareability, clarity, payoff); viral_score is their sum.
2. Audience signals from YouTube (no extra AI cost):
   - heatmap   "Most replayed" graph, 0..1 per time bin (yt-dlp ``heatmap``)
   - chapters  uploader chapters (yt-dlp ``chapters``)
   - comments  timestamps viewers wrote in comments, e.g. "3:15 😂" (opt-in)

Signals are used twice: as [AUDIENCE ...] markers in the transcript so the AI
can see proven moments, and in a combined score that re-ranks the AI's
candidates:

    final = sum(weight[k] * component[k]) / sum(weight[k])   over available k

Every component is saved on the clip so the ranking is always explainable.
"""

import json
import math
import re

from clipping import events as events_mod

BREAKDOWN_KEYS = ("hook", "emotion", "shareability", "clarity", "payoff")
DEFAULT_WEIGHTS = {"ai": 0.5, "heatmap": 0.3, "comments": 0.2, "audio": 0.1, "visual": 0.3}
HEATMAP_MARK_THRESHOLD = 0.5   # mark transcript lines replayed at >= 50% of the peak
COMMENT_WINDOW_PAD = 3.0       # seconds of slack around a commented timestamp
MAX_OVERLAP = 0.5              # re-ranking skips candidates sharing >50% with a better clip
MIN_COMMENT_MARKS = 5          # fewer comment timestamps than this is too thin to rank with

_LINE_RE = re.compile(r"^\[(\d+(?:\.\d+)?) - (\d+(?:\.\d+)?)\]")
_TS_RE = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,2}):([0-5]\d)(?![\d:])")


# ---------------------------------------------------------------------------
# Score breakdown
# ---------------------------------------------------------------------------

def apply_score_breakdown(clips: list[dict]) -> list[dict]:
    """Clamp each component to 1-20 and set viral_score to their sum."""
    for clip in clips:
        sb = clip.get("score_breakdown")
        if not isinstance(sb, dict) or not all(k in sb for k in BREAKDOWN_KEYS):
            continue
        cleaned = {}
        for key in BREAKDOWN_KEYS:
            try:
                cleaned[key] = max(1, min(20, int(round(float(sb[key])))))
            except (TypeError, ValueError):
                cleaned[key] = 1
        clip["score_breakdown"] = cleaned
        clip["viral_score"] = sum(cleaned.values())
    return clips


# ---------------------------------------------------------------------------
# Collecting signals
# ---------------------------------------------------------------------------

def parse_comment_timestamps(text: str, duration: float | None = None) -> list[float]:
    """Return the timestamps (seconds) mentioned in a comment."""
    out = []
    for h, m, s in _TS_RE.findall(text or ""):
        secs = int(h or 0) * 3600 + int(m) * 60 + int(s)
        if duration is None or secs <= duration:
            out.append(float(secs))
    return out


def collect_signals(info: dict | None, comments: list[dict] | None = None) -> dict:
    """Build the signals dict from a yt-dlp info dict (and optional comments)."""
    info = info or {}
    duration = info.get("duration")
    heatmap = []
    for b in info.get("heatmap") or []:
        try:
            heatmap.append({"start": float(b["start_time"]), "end": float(b["end_time"]),
                            "value": max(0.0, min(1.0, float(b["value"])))})
        except (KeyError, TypeError, ValueError):
            continue
    if heatmap:
        peak = max(b["value"] for b in heatmap) or 1.0
        for b in heatmap:  # normalize so the most replayed bin is 1.0
            b["value"] = round(b["value"] / peak, 4)

    chapters = []
    for c in info.get("chapters") or []:
        try:
            chapters.append({"start": float(c["start_time"]), "end": float(c["end_time"]),
                             "title": str(c.get("title", "")).strip()})
        except (KeyError, TypeError, ValueError):
            continue

    marks = []
    for c in comments or []:
        likes = c.get("like_count") or 0
        weight = round(1.0 + math.log10(1 + max(0, int(likes))), 3)
        for t in parse_comment_timestamps(c.get("text", ""), duration):
            marks.append({"time": t, "weight": weight})

    return {"duration": duration, "heatmap": heatmap, "chapters": chapters,
            "comment_marks": marks, "comments_fetched": len(comments or [])}


def fetch_comments(url: str, max_comments: int = 300, cookies=None) -> list[dict]:
    """Fetch top comments with yt-dlp (one extra request; may be rate-limited)."""
    from yt_dlp import YoutubeDL

    opts = {
        "quiet": True,
        "skip_download": True,
        "getcomments": True,
        "extractor_args": {"youtube": {"max_comments": [str(max_comments), str(max_comments), "0", "0"],
                                       "comment_sort": ["top"]}},
    }
    if cookies:
        opts["cookiefile"] = cookies
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return [{"text": c.get("text", ""), "like_count": c.get("like_count") or 0}
            for c in (info or {}).get("comments") or []]


def _comments_usable(signals: dict | None) -> bool:
    return bool(signals) and len(signals.get("comment_marks") or []) >= MIN_COMMENT_MARKS


def has_signals(signals: dict | None) -> bool:
    """True when the signals are strong enough to re-rank clips."""
    return bool(signals and (signals.get("heatmap") or _comments_usable(signals)
                             or (signals.get("events") or {}).get("spikes")))


def save_signals(signals: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(signals, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Per-window measurements
# ---------------------------------------------------------------------------

def heatmap_in_window(signals: dict, start: float, end: float) -> float | None:
    """Peak replay value (0..1) overlapping [start, end]; None if no heatmap."""
    bins = signals.get("heatmap") or []
    if not bins:
        return None
    vals = [b["value"] for b in bins if b["end"] > start and b["start"] < end]
    return max(vals) if vals else 0.0


def comment_weight_in_window(signals: dict, start: float, end: float) -> float:
    return sum(m["weight"] for m in signals.get("comment_marks") or []
               if start - COMMENT_WINDOW_PAD <= m["time"] <= end + COMMENT_WINDOW_PAD)


def comment_count_in_window(signals: dict, start: float, end: float) -> int:
    return sum(1 for m in signals.get("comment_marks") or []
               if start - COMMENT_WINDOW_PAD <= m["time"] <= end + COMMENT_WINDOW_PAD)


# ---------------------------------------------------------------------------
# Transcript markers
# ---------------------------------------------------------------------------

def annotate_transcript(transcript: str, signals: dict | None) -> str:
    """Append [AUDIENCE ...] / [EVENT ...] markers and insert [CHAPTER ...] lines."""
    ev = (signals or {}).get("events")
    if not signals or not (signals.get("heatmap") or signals.get("comment_marks")
                           or signals.get("chapters") or events_mod.has_events(ev)):
        return transcript
    chapters = sorted(signals.get("chapters") or [], key=lambda c: c["start"])
    ci = 0
    out = []
    for line in transcript.splitlines():
        m = _LINE_RE.match(line)
        if not m:
            out.append(line)
            continue
        start, end = float(m.group(1)), float(m.group(2))
        while ci < len(chapters) and chapters[ci]["start"] <= end:
            out.append(f"[CHAPTER at {chapters[ci]['start']:.1f}s] {chapters[ci]['title']}")
            ci += 1
        tags = []
        replay = heatmap_in_window(signals, start, end)
        if replay is not None and replay >= HEATMAP_MARK_THRESHOLD:
            tags.append(f"replayed {round(replay * 100)}%")
        n = comment_count_in_window(signals, start, end)
        if n:
            tags.append(f"{n} viewer comment{'s' if n != 1 else ''} point here")
        event_tags = events_mod.tags_for_window(ev, start, end) if ev else []
        out.append(line + (f" [AUDIENCE: {', '.join(tags)}]" if tags else "")
                   + (f" [EVENT: {', '.join(event_tags)}]" if event_tags else ""))
    result = "\n".join(out)
    return result + ("\n" if transcript.endswith("\n") else "")


# ---------------------------------------------------------------------------
# Combined score and re-ranking
# ---------------------------------------------------------------------------

def parse_weights(text: str | None) -> dict:
    """Parse "ai=0.5,heatmap=0.3,comments=0.2" (missing keys keep defaults)."""
    weights = dict(DEFAULT_WEIGHTS)
    for part in (text or "").split(","):
        if not part.strip():
            continue
        key, _, val = part.partition("=")
        key = key.strip().lower()
        if key not in DEFAULT_WEIGHTS:
            raise ValueError(f"Unknown score weight {key!r}; use {', '.join(DEFAULT_WEIGHTS)}.")
        try:
            weights[key] = float(val)
        except ValueError:
            raise ValueError(f"Score weight {key!r} needs a number, got {val!r}.") from None
        if weights[key] < 0:
            raise ValueError(f"Score weight {key!r} must not be negative.")
    if weights["ai"] <= 0:
        raise ValueError("Score weight 'ai' must be greater than 0.")
    return weights


def rerank(clips: list[dict], signals: dict | None, keep: int, weights: dict | None = None) -> list[dict]:
    """Combine AI score with audience signals, sort, keep the top *keep* clips.

    viral_score becomes the combined score (0-100); the AI's own score is kept
    in ai_viral_score and every component in score_components.
    """
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    have_heatmap = bool(signals and signals.get("heatmap"))
    have_comments = _comments_usable(signals)
    ev = (signals or {}).get("events")
    have_audio = bool(ev and ev.get("spikes"))
    have_visual = any("visual_score" in c for c in clips)

    measured = []
    for clip in clips:
        start, end = float(clip.get("start_time", 0)), float(clip.get("end_time", 0))
        ai = max(0.0, min(1.0, float(clip.get("ai_viral_score", clip.get("viral_score", 0)) or 0) / 100))
        comps = {"ai": round(ai, 4)}
        if have_heatmap:
            comps["heatmap"] = round(heatmap_in_window(signals, start, end) or 0.0, 4)
        if have_comments:
            # saturating: 1 mention ~0.28, 3 ~0.63, 6+ ~0.86
            comps["comments"] = round(1 - math.exp(-comment_weight_in_window(signals, start, end) / 3), 4)
        if have_audio:
            comps["audio"] = events_mod.audio_score(ev, start, end)
        if have_visual:
            comps["visual"] = round(max(0.0, min(1.0, float(clip.get("visual_score", 0)) / 100)), 4)
        measured.append(comps)

    # A signal that is 0 for every candidate can't tell them apart; counting it
    # would only drag every score down, so it is dropped.
    informative = {"ai"} | {k for k in ("heatmap", "comments", "audio", "visual")
                            if any(c.get(k, 0) > 0 for c in measured) and weights.get(k, 0) > 0}

    for clip, comps in zip(clips, measured):
        start, end = float(clip.get("start_time", 0)), float(clip.get("end_time", 0))
        used = {k: v for k, v in comps.items() if k in informative}
        total_w = sum(weights[k] for k in used)
        final = sum(weights[k] * v for k, v in used.items()) / total_w
        clip.setdefault("ai_viral_score", clip.get("viral_score", 0))
        clip["score_components"] = used
        clip["audience_signals"] = {
            "heatmap_peak": comps.get("heatmap"),
            "comment_mentions": comment_count_in_window(signals, start, end) if have_comments else None,
            "event_tags": events_mod.tags_for_window(ev, start, end) if ev else [],
        }
        clip["viral_score"] = int(round(final * 100))

    ranked = []
    for clip in sorted(clips, key=lambda c: c["viral_score"], reverse=True):
        if len(ranked) >= max(1, keep):
            break
        if any(_overlap_ratio(clip, kept) > MAX_OVERLAP for kept in ranked):
            continue  # near-duplicate of a better candidate
        ranked.append(clip)
    for i, clip in enumerate(ranked, 1):
        clip["rank"] = i
    return ranked


def _overlap_ratio(a: dict, b: dict) -> float:
    """Shared time as a fraction of the shorter clip."""
    a0, a1 = float(a.get("start_time", 0)), float(a.get("end_time", 0))
    b0, b1 = float(b.get("start_time", 0)), float(b.get("end_time", 0))
    shared = max(0.0, min(a1, b1) - max(a0, b0))
    shorter = min(a1 - a0, b1 - b0)
    return shared / shorter if shorter > 0 else 0.0


def candidate_pool(requested: int, signals: dict | None, force: bool = False) -> int:
    """Ask the AI for extra candidates when signals (or visual review) can re-rank them."""
    if not (force or has_signals(signals)):
        return requested
    return min(requested * 2, requested + 5)
