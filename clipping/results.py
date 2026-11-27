"""
clipping.results — Read render results without loading the heavy pipeline.

Kept dependency-free (stdlib only) so the web app can list projects even when
it is started without the pipeline's packages (Whisper, OpenCV, ...).
"""

import os


def summarize_render(manifest: list[dict] | None) -> tuple[int, int, list[str]]:
    """(succeeded, failed, report lines) for a render manifest.

    A clip only counts as rendered when its status is "success" AND the video
    file exists on disk.
    """
    ok, failed, lines = 0, 0, []
    for row in manifest or []:
        rendered = row.get("status") == "success" and os.path.exists(row.get("video_path", ""))
        if rendered:
            ok += 1
            lines.append(f"   ✅ #{row.get('rank')}  {row.get('duration', '?')}s  {row.get('video_path')}")
        else:
            failed += 1
            reason = row.get("error") or ("video file missing" if row.get("status") == "success" else "unknown error")
            lines.append(f"   ❌ #{row.get('rank')}  FAILED: {str(reason).strip().splitlines()[-1][:200]}")
    return ok, failed, lines
