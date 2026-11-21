"""
clipping.visual — Let Gemini SEE the top candidates and score them visually.

The clip picker only reads the transcript. This step samples a few small
frames from each candidate (FFmpeg, CPU) and sends them, with the transcript
excerpt and title, to Gemini in ONE extra request. Gemini returns a
visual_score (0-100) and a short note per candidate, judged on what is
visible: action/emotion, visual hook, a clear subject that crops well to 9:16,
or dull visuals (static slides, black frames, logos).

The visual_score becomes the "visual" component of the combined score
(clipping.scoring.rerank). Opt-in with --visual-rerank; Gemini only.
"""

import re
import subprocess

DEFAULT_FRAMES = 6
FRAME_WIDTH = 512
_LINE_RE = re.compile(r"^\[(\d+(?:\.\d+)?) - (\d+(?:\.\d+)?)\]\s*(.*)$")

RESPONSE_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "candidate": {"type": "INTEGER"},
            "visual_score": {"type": "INTEGER"},
            "visual_notes": {"type": "STRING"},
        },
        "required": ["candidate", "visual_score", "visual_notes"],
    },
}

INSTRUCTIONS = """You are a short-form video editor reviewing candidate clips cut from ONE video for TikTok, Reels and YouTube Shorts (vertical 9:16).

For each candidate you get its number, time range, title, a transcript excerpt and {frames} frames sampled evenly across the clip, in order.

Judge what you can SEE and how well it supports the words:
- Visual hook: would the first frames stop someone scrolling?
- Visible action or emotion: reactions, celebrations, gestures, movement, expressive faces.
- Visual variety across the frames versus one static shot.
- A clear main subject (face or action) that can be cropped to vertical 9:16 without losing it.
- Penalize dull visuals: static slides, text-only screens, black/blurred frames, logos, empty scenes.

Give each candidate a visual_score from 0 to 100. Be strict and COMPARATIVE across the candidates so the scores separate them. visual_notes: at most 20 words on what you saw.
Return a JSON array with one object per candidate: candidate, visual_score, visual_notes."""


def frame_times(start: float, end: float, n: int = DEFAULT_FRAMES) -> list[float]:
    """n timestamps evenly spread inside (start, end), avoiding the exact edges."""
    if end <= start or n < 1:
        return []
    step = (end - start) / n
    return [round(start + step * (i + 0.5), 2) for i in range(n)]


def extract_frame(video_path: str, t: float, width: int = FRAME_WIDTH, timeout: int = 60) -> bytes:
    """One JPEG frame at time t (fast seek), scaled to *width* px."""
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", video_path,
         "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "5", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
    )
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip()[-200:] or "no frame")
    return result.stdout


def transcript_excerpt(transcript: str, start: float, end: float, max_chars: int = 600) -> str:
    words = []
    for line in transcript.splitlines():
        m = _LINE_RE.match(line)
        if m and float(m.group(2)) > start and float(m.group(1)) < end:
            words.append(m.group(3).strip())
    text = " ".join(w for w in words if w)
    return text if len(text) <= max_chars else text[:max_chars].rsplit(" ", 1)[0] + " ..."


def build_parts(clips: list[dict], video_path: str, transcript: str, types, frames: int = DEFAULT_FRAMES):
    """Gemini content parts: instructions, then per candidate a text header + its frames."""
    parts = [types.Part.from_text(text=INSTRUCTIONS.format(frames=frames))]
    for i, clip in enumerate(clips, 1):
        start, end = float(clip.get("start_time", 0)), float(clip.get("end_time", 0))
        title = clip.get("title_inggris") or clip.get("title_indonesia") or ""
        parts.append(types.Part.from_text(text=(
            f"\nCANDIDATE {i} | {start:.1f}s - {end:.1f}s | title: {title}\n"
            f"transcript: {transcript_excerpt(transcript, start, end)}\n"
            f"frames ({frames}, in order):"
        )))
        for t in frame_times(start, end, frames):
            parts.append(types.Part.from_bytes(data=extract_frame(video_path, t), mime_type="image/jpeg"))
    return parts


def apply_scores(clips: list[dict], response) -> int:
    """Attach visual_score / visual_notes from Gemini's answer. Returns clips scored."""
    if not isinstance(response, list):
        return 0
    scored = 0
    for item in response:
        try:
            idx = int(item["candidate"]) - 1
            score = max(0, min(100, int(item["visual_score"])))
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= idx < len(clips):
            clips[idx]["visual_score"] = score
            clips[idx]["visual_notes"] = str(item.get("visual_notes", ""))[:200]
            scored += 1
    return scored


def score_candidates(clips: list[dict], video_path: str, transcript: str, cfg, generate_json,
                     timeout_ms: int = 5 * 60 * 1000) -> int:
    """Run the visual review. ``generate_json`` is engine._generate_json_with_retry."""
    import google.genai as genai
    from google.genai import types

    frames = getattr(cfg, "visual_frames", DEFAULT_FRAMES)
    parts = build_parts(clips, video_path, transcript, types, frames)
    print(f"👁️ Visual review: {len(clips)} kandidat x {frames} frame dikirim ke Gemini...")
    client = genai.Client(
        api_key=cfg.api_key_gemini,
        vertexai=False,
        http_options=types.HttpOptions(api_version="v1beta", timeout=timeout_ms,
                                       retry_options=types.HttpRetryOptions(attempts=1)),
    )
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=RESPONSE_SCHEMA,
        temperature=getattr(cfg, "ai_temperature", None),
    )
    response = generate_json(
        client=client,
        model=cfg.gemini_model,
        fallback_model=getattr(cfg, "gemini_fallback_model", None),
        contents=[types.Content(role="user", parts=parts)],
        config=config,
    )
    return apply_scores(clips, response)
