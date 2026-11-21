"""
clipping.events — Audio/visual "event" detection with FFmpeg (CPU, no AI).

Gives the AI "eyes and ears" for moments the transcript can't describe:

- Volume spikes: loudness is measured every second (RMS, dB). Seconds well
  above the video's typical level (crowd roar, laughter, shouting, a big hit)
  are grouped into spikes.
- Scene-cut bursts: hard cuts are detected on a small, low-fps copy of the
  video. Several cuts within a few seconds usually mean a replay, montage or
  dramatic edit.

Both run as a single FFmpeg pass each and work on every video, unlike YouTube
heatmaps. Failures never stop the pipeline; they return no events.
"""

import re
import statistics
import subprocess

SPIKE_DB = 6.0          # a second is "loud" when >= its surrounding median + 6 dB
LOCAL_WINDOW = 31       # seconds of context for that median
SPIKE_GAP = 1.0         # loud seconds closer than this merge into one spike
SCENE_THRESHOLD = 0.35  # FFmpeg scene score for a hard cut (0..1)
BURST_WINDOW = 6.0      # cuts within this many seconds form a burst...
BURST_MIN_CUTS = 3      # ...when there are at least this many

_PTS_RE = re.compile(r"pts_time:([0-9.]+)")
_RMS_RE = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(-?inf|-?[0-9.]+)")
_SCENE_RE = re.compile(r"lavfi\.scene_score=([0-9.]+)")


def _run_ffmpeg(args: list[str], timeout: int) -> str:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error", *args],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[-300:] or f"ffmpeg exit {result.returncode}")
    return result.stdout


def parse_loudness(text: str) -> list[tuple[float, float]]:
    """Parse ametadata output into [(second, rms_db)]; silence becomes -90 dB."""
    out, t = [], None
    for line in text.splitlines():
        m = _PTS_RE.search(line)
        if m:
            t = float(m.group(1))
            continue
        m = _RMS_RE.search(line)
        if m and t is not None:
            val = m.group(1)
            out.append((t, -90.0 if "inf" in val else max(-90.0, float(val))))
            t = None
    return out


def parse_scene_cuts(text: str, threshold: float = SCENE_THRESHOLD) -> list[float]:
    cuts, t = [], None
    for line in text.splitlines():
        m = _PTS_RE.search(line)
        if m:
            t = float(m.group(1))
            continue
        m = _SCENE_RE.search(line)
        if m and t is not None:
            if float(m.group(1)) >= threshold:
                cuts.append(round(t, 2))
            t = None
    return cuts


def measure_loudness(video_path: str, timeout: int = 900) -> list[tuple[float, float]]:
    """One RMS reading per second of audio (audio-only decode, fast)."""
    text = _run_ffmpeg([
        "-i", video_path, "-vn",
        "-af", "aresample=8000,asetnsamples=n=8000:p=0,astats=metadata=1:reset=1,"
               "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
        "-f", "null", "-",
    ], timeout)
    return parse_loudness(text)


def detect_scene_cuts(video_path: str, fps: int = 4, width: int = 256, timeout: int = 1800) -> list[float]:
    """Hard-cut timestamps, computed on a small low-fps copy to stay cheap on CPU."""
    text = _run_ffmpeg([
        "-i", video_path, "-an",
        "-vf", f"fps={fps},scale={width}:-2,select='gte(scene,0)',"
               "metadata=print:key=lavfi.scene_score:file=-",
        "-f", "null", "-",
    ], timeout)
    return parse_scene_cuts(text)


def find_spikes(loudness: list[tuple[float, float]]) -> tuple[float | None, list[dict]]:
    """Group loud seconds into spikes. Returns (overall median dB, spikes).

    Each second is compared with the median of the surrounding LOCAL_WINDOW
    seconds, so quiet intros or loud outros don't skew detection. Most YouTube
    uploads are loudness-compressed (~2-3 dB range), so spikes mainly fire on
    less-processed audio (streams, gameplay, raw podcasts); that is intended,
    a lower threshold would mostly mark noise.
    """
    voiced = [db for _, db in loudness if db > -60]
    if len(voiced) < 10:
        return None, []
    baseline = statistics.median(voiced)
    half = LOCAL_WINDOW // 2
    dbs = [db for _, db in loudness]
    spikes = []
    for i, (t, db) in enumerate(loudness):
        around = [x for x in dbs[max(0, i - half):i + half + 1] if x > -60]
        local = statistics.median(around) if len(around) >= 5 else baseline
        delta = db - local
        if delta < SPIKE_DB:
            continue
        if spikes and t - spikes[-1]["end"] <= SPIKE_GAP:
            spikes[-1]["end"] = t + 1.0
            spikes[-1]["delta_db"] = max(spikes[-1]["delta_db"], round(delta, 1))
        else:
            spikes.append({"start": t, "end": t + 1.0, "delta_db": round(delta, 1)})
    return round(baseline, 1), spikes


def find_cut_bursts(cuts: list[float]) -> list[dict]:
    """Windows with >= BURST_MIN_CUTS cuts within BURST_WINDOW seconds (merged)."""
    bursts = []
    cuts = sorted(cuts)
    i = 0
    for j in range(len(cuts)):
        while cuts[j] - cuts[i] > BURST_WINDOW:
            i += 1
        if j - i + 1 >= BURST_MIN_CUTS:
            start, end, count = cuts[i], cuts[j], j - i + 1
            if bursts and start <= bursts[-1]["end"]:
                bursts[-1]["end"] = end
                bursts[-1]["cuts"] = len([c for c in cuts if bursts[-1]["start"] <= c <= end])
            else:
                bursts.append({"start": start, "end": end, "cuts": count})
    return bursts


def collect_events(video_path: str, scenes: bool = True) -> dict:
    """Run detectors; any failure leaves that part empty."""
    events = {"baseline_db": None, "spikes": [], "cuts": [], "cut_bursts": [], "errors": []}
    try:
        events["baseline_db"], events["spikes"] = find_spikes(measure_loudness(video_path))
    except Exception as exc:  # noqa: BLE001 - events are a bonus signal
        events["errors"].append(f"loudness: {exc}")
    if scenes:
        try:
            events["cuts"] = detect_scene_cuts(video_path)
            events["cut_bursts"] = find_cut_bursts(events["cuts"])
        except Exception as exc:  # noqa: BLE001
            events["errors"].append(f"scenes: {exc}")
    return events


def has_events(events: dict | None) -> bool:
    return bool(events and (events.get("spikes") or events.get("cut_bursts")))


def tags_for_window(events: dict | None, start: float, end: float) -> list[str]:
    """Human-readable event tags overlapping [start, end] (for transcript markers)."""
    if not events:
        return []
    tags = []
    deltas = [s["delta_db"] for s in events.get("spikes") or [] if s["end"] > start and s["start"] < end]
    if deltas:
        tags.append(f"volume spike +{max(deltas):.0f} dB")
    cuts = [b["cuts"] for b in events.get("cut_bursts") or [] if b["end"] >= start and b["start"] <= end]
    if cuts:
        tags.append(f"{max(cuts)} rapid scene cuts")
    return tags


def audio_score(events: dict | None, start: float, end: float) -> float:
    """0..1 from the biggest volume spike in the window (+20 dB or more = 1.0)."""
    deltas = [s["delta_db"] for s in (events or {}).get("spikes") or [] if s["end"] > start and s["start"] < end]
    return round(min(1.0, max(deltas) / 20.0), 4) if deltas else 0.0
