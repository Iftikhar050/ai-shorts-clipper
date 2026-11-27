"""
clipping.postprocess — Finishing touches on rendered clips.

Loudness normalization: YouTube, TikTok and Instagram play audio at about
-14 LUFS. Clips cut from different sources (or different parts of one video)
otherwise jump in volume. This re-encodes only the audio track (video is
copied untouched), so it is fast and lossless for the picture.
"""

import os
import subprocess

TARGET_LUFS = -14.0
TRUE_PEAK = -1.0


def normalize_loudness(video_path: str, target: float = TARGET_LUFS, timeout: int = 600) -> bool:
    """Normalize the clip's audio in place. Returns False (file untouched) on any failure."""
    if not os.path.isfile(video_path):
        return False
    tmp = video_path + ".loudnorm.mp4"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", video_path,
        "-map", "0", "-c:v", "copy",
        "-af", f"loudnorm=I={target}:TP={TRUE_PEAK}:LRA=11",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", tmp,
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        if result.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            return False
        os.replace(tmp, video_path)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
