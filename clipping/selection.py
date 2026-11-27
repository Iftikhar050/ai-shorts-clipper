"""
clipping.selection — User controls over which clips are produced.

- Clip length presets (--clip-length) and enforcement after the AI answers:
  the AI is told the range, but models drift, so out-of-range clips are
  dropped when enough in-range candidates exist.
- Time range (--time-range 2:00-10:30): only that part of the video is sent
  to the AI, so every clip comes from it.
"""

import re

LENGTH_PRESETS = {
    "short": (15, 30),    # punchy Shorts / Reels
    "medium": (30, 60),   # default: the common Shorts sweet spot
    "long": (60, 90),
    "any": (20, 179),     # upstream behaviour
}
DURATION_TOLERANCE = 5.0  # seconds of slack around the requested range

_LINE_RE = re.compile(r"^\[(\d+(?:\.\d+)?) - (\d+(?:\.\d+)?)\]")


def parse_timestamp(text: str) -> float:
    """'90', '1:30', '01:02:03' or '1:30.5' -> seconds."""
    text = text.strip()
    if not text or not re.fullmatch(r"\d+(?::\d{1,2}){0,2}(?:\.\d+)?", text):
        raise ValueError(f"Invalid time {text!r}; use seconds, mm:ss or hh:mm:ss.")
    parts = text.split(":")
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def parse_time_range(text: str | None) -> tuple[float, float] | None:
    """'2:00-10:30' -> (120.0, 630.0). None when not given."""
    if not text or not text.strip():
        return None
    start_s, sep, end_s = text.partition("-")
    if not sep:
        raise ValueError("Time range needs a start and end, e.g. 2:00-10:30.")
    start, end = parse_timestamp(start_s), parse_timestamp(end_s)
    if end <= start:
        raise ValueError(f"Time range end ({end_s.strip()}) must be after start ({start_s.strip()}).")
    return start, end


def resolve_lengths(preset: str, min_duration: int | None, max_duration: int | None) -> tuple[int, int]:
    lo, hi = LENGTH_PRESETS[preset]
    lo = min_duration if min_duration is not None else lo
    hi = max_duration if max_duration is not None else hi
    if lo < 5 or hi <= lo:
        raise ValueError(f"Clip length range {lo}-{hi}s is invalid (min >= 5, max > min).")
    return int(lo), int(hi)


def filter_transcript(transcript: str, time_range: tuple[float, float] | None) -> str:
    """Keep only transcript lines that overlap the time range (marker lines follow their line)."""
    if not time_range:
        return transcript
    start, end = time_range
    out, keep = [], False
    for line in transcript.splitlines():
        m = _LINE_RE.match(line)
        if m:
            keep = float(m.group(2)) > start and float(m.group(1)) < end
        if keep:
            out.append(line)
    return "\n".join(out) + ("\n" if out and transcript.endswith("\n") else "")


def enforce_duration(clips: list[dict], lo: float, hi: float, keep: int) -> tuple[list[dict], list[dict]]:
    """Split clips into (accepted, rejected) by length.

    Out-of-range clips are only rejected while at least *keep* in-range clips
    remain; otherwise the closest ones are kept so the user still gets clips.
    """
    def length(c):
        return float(c.get("end_time", 0)) - float(c.get("start_time", 0))

    def distance(c):
        d = length(c)
        return 0.0 if lo - DURATION_TOLERANCE <= d <= hi + DURATION_TOLERANCE else min(abs(d - lo), abs(d - hi))

    inside = [c for c in clips if distance(c) == 0.0]
    outside = sorted((c for c in clips if distance(c) > 0.0), key=distance)
    need = max(0, min(keep, len(clips)) - len(inside))
    accepted = inside + outside[:need]
    rejected = outside[need:]
    accepted_ids = {id(c) for c in accepted}
    return [c for c in clips if id(c) in accepted_ids], rejected
