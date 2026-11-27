"""
app.server — Local web interface for ai-shorts-clipper.

    python run_app.py            # opens http://127.0.0.1:8765

Binds to 127.0.0.1 only: there is no login, so it must not be exposed to a
network. API keys are written to .env but never sent back to the browser.
"""

import json
import os
import re
import secrets
import shutil
import subprocess

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import jobs as J

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
UPLOADS_DIR = os.path.join(J.DATA_DIR, "uploads")
SETTINGS_FILE = os.path.join(J.DATA_DIR, "settings.json")
ENV_FILE = os.path.join(J.ROOT, ".env")
ACCOUNTS_FILE = os.path.join(J.ROOT, "accounts.json")
ACCOUNTS_EXAMPLE = os.path.join(J.ROOT, "accounts.example.json")
EDITABLE_KEYS = ("GOOGLE_API_KEY", "PEXELS_API_KEY")
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
MAX_UPLOAD_BYTES = 8 * 1024 ** 3
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_ID = re.compile(r"^(?:cli-)?[A-Za-z0-9._-]+$")
_YT_ID = re.compile(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})")


def load_settings() -> dict:
    data = J._read_json(SETTINGS_FILE) or {}
    return {k: data.get(k, v) for k, v in J.DEFAULT_SETTINGS.items()}


def create_app(store: J.JobStore | None = None, runner: J.JobRunner | None = None, start_runner: bool = True) -> FastAPI:
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    store = store or J.JobStore()
    runner = runner or J.JobRunner(store, settings_loader=load_settings)
    if start_runner:
        runner.start()

    app = FastAPI(title="AI Shorts Clipper", docs_url=None, redoc_url=None)
    app.state.store, app.state.runner = store, runner

    # ------------------------------------------------------------------ jobs
    @app.get("/api/jobs")
    def list_jobs():
        own = [_public(j, runner) for j in store.all()]
        known = {os.path.normcase(os.path.abspath(j["output_dir"])) for j in store.all()}
        return {"jobs": own + _discover_cli_runs(known)}

    @app.post("/api/jobs")
    async def create_job(request: Request):
        body = await _json(request)
        try:
            source = body.get("source") or ""
            upload_id = body.get("upload_id")
            if upload_id:
                source = _upload_path(upload_id)
            source = J.validate_source(source)
            options = J.validate_options(body.get("options"))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        title = body.get("title") or (os.path.basename(source) if upload_id else source)
        return _public(runner.submit(source, options, title=str(title)[:200]), runner)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return _public(_job_or_404(store, job_id), runner)

    @app.get("/api/jobs/{job_id}/log")
    def get_log(job_id: str, offset: int = 0):
        _job_or_404(store, job_id)
        text, nxt = J.read_log(job_id, store, offset)
        return {"text": text, "offset": nxt}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        _job_or_404(store, job_id)
        if not runner.cancel(job_id):
            raise HTTPException(409, "Only queued or running jobs can be cancelled.")
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/rerun")
    def rerun_job(job_id: str):
        job = _job_or_404(store, job_id)
        return _public(runner.submit(job["source"], job["options"], title=job["title"]), runner)

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str, files: bool = False):
        job = _job_or_404(store, job_id)
        if job["status"] in ("queued", "running"):
            raise HTTPException(409, "Cancel the job before deleting it.")
        if files:
            out = os.path.abspath(job["output_dir"])
            if os.path.dirname(out) == os.path.abspath(J.OUTPUTS_DIR) and os.path.basename(out) == job_id:
                shutil.rmtree(out, ignore_errors=True)
        store.remove(job_id)
        try:
            os.remove(store.log_path(job_id))
        except OSError:
            pass
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/clips")
    def get_clips(job_id: str):
        job = _job_any(store, job_id)
        return {"clips": _clips_for(job_id, job["output_dir"])}

    # ---------------------------------------------------------------- media
    @app.get("/media/{job_id}/{name}")
    def media(job_id: str, name: str):
        job = _job_any(store, job_id)
        if not _SAFE_NAME.match(name) or not name.lower().endswith((".mp4", ".jpg", ".jpeg", ".png", ".json", ".srt")):
            raise HTTPException(404)
        folder = os.path.abspath(job["output_dir"])
        path = os.path.abspath(os.path.join(folder, name))
        if os.path.dirname(path) != folder or not os.path.isfile(path):
            raise HTTPException(404)
        return FileResponse(path)

    # --------------------------------------------------------------- upload
    @app.post("/api/uploads")
    async def upload(file: UploadFile = File(...)):
        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext not in VIDEO_EXTS:
            raise HTTPException(422, f"Unsupported file type {ext or '(none)'}; use {', '.join(sorted(VIDEO_EXTS))}.")
        upload_id = secrets.token_hex(8) + ext
        path = os.path.join(UPLOADS_DIR, upload_id)
        size = 0
        with open(path, "wb") as out:
            while chunk := await file.read(4 * 1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    out.close()
                    os.remove(path)
                    raise HTTPException(413, "File is larger than 8 GB.")
                out.write(chunk)
        return {"upload_id": upload_id, "name": file.filename, "size": size}

    # ------------------------------------------------------------- settings
    @app.get("/api/settings")
    def get_settings():
        env = _read_env()
        keys = {k: {"set": bool(_real(env.get(k))), "hint": _mask(env.get(k))} for k in EDITABLE_KEYS}
        return {
            "keys": keys,
            "settings": load_settings(),
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "accounts_configured": os.path.isfile(ACCOUNTS_FILE),
        }

    @app.put("/api/settings")
    async def put_settings(request: Request):
        body = await _json(request)
        current = load_settings()
        for k, v in (body.get("settings") or {}).items():
            if k not in J.DEFAULT_SETTINGS:
                raise HTTPException(422, f"Unknown setting {k}")
            v = str(v).strip()
            if not v or len(v) > 80 or not re.fullmatch(r"[A-Za-z0-9._:/-]+", v):
                raise HTTPException(422, f"Invalid value for {k}")
            current[k] = v
        if current["whisper_device"] not in ("cpu", "cuda", "auto"):
            raise HTTPException(422, "whisper_device must be cpu, cuda or auto")
        os.makedirs(J.DATA_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(current, fh, indent=2)
        keys = body.get("keys") or {}
        for k, v in keys.items():
            if k not in EDITABLE_KEYS:
                raise HTTPException(422, f"{k} can't be edited here")
            v = str(v).strip()
            if v and (len(v) > 200 or not re.fullmatch(r"[A-Za-z0-9._-]+", v)):
                raise HTTPException(422, f"{k} doesn't look like an API key")
        if keys:
            _write_env({k: str(v).strip() for k, v in keys.items()})
        return get_settings()

    # ------------------------------------------------------------- accounts
    @app.get("/api/accounts")
    def get_accounts():
        path = ACCOUNTS_FILE if os.path.isfile(ACCOUNTS_FILE) else ACCOUNTS_EXAMPLE
        data = J._read_json(path) or {"accounts": {}, "rules": []}
        return {"configured": path == ACCOUNTS_FILE, "data": data}

    @app.put("/api/accounts")
    async def put_accounts(request: Request):
        from clipping.accounts import AccountsError, load_accounts

        body = await _json(request)
        tmp = ACCOUNTS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(body, fh, ensure_ascii=False, indent=2)
        try:
            load_accounts(tmp)
        except AccountsError as exc:
            os.remove(tmp)
            raise HTTPException(422, str(exc)) from None
        os.replace(tmp, ACCOUNTS_FILE)
        return get_accounts()

    @app.delete("/api/accounts")
    def reset_accounts():
        if os.path.isfile(ACCOUNTS_FILE):
            os.remove(ACCOUNTS_FILE)
        return get_accounts()

    # --------------------------------------------------------------- static
    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc: HTTPException):
        return JSONResponse({"detail": exc.detail or "Not found"}, status_code=exc.status_code)

    return app


# ----------------------------------------------------------------- helpers

async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Body must be JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "Body must be a JSON object")
    return body


def _job_or_404(store, job_id):
    job = store.get(job_id) if _SAFE_ID.match(job_id or "") else None
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def _job_any(store, job_id):
    """A web job, or a folder from an earlier command-line run (id 'cli-<folder>')."""
    if job_id.startswith("cli-") and _SAFE_NAME.match(job_id[4:]):
        folder = os.path.join(J.OUTPUTS_DIR, job_id[4:])
        if os.path.isfile(os.path.join(folder, "metadata_preview.json")):
            return {"id": job_id, "output_dir": folder}
        raise HTTPException(404, "Run not found")
    return _job_or_404(store, job_id)


def _upload_path(upload_id: str) -> str:
    if not _SAFE_NAME.match(upload_id or ""):
        raise ValueError("Invalid upload")
    path = os.path.join(UPLOADS_DIR, upload_id)
    if not os.path.isfile(path):
        raise ValueError("Upload not found; please upload the file again.")
    return path


def _youtube_id(source: str) -> str | None:
    m = _YT_ID.search(source or "")
    return m.group(1) if m else None


def _public(job: dict, runner: J.JobRunner | None = None) -> dict:
    out = {k: v for k, v in job.items() if k != "output_dir"}
    out["youtube_id"] = _youtube_id(job.get("source", ""))
    out["is_upload"] = os.path.isabs(job.get("source", "")) and not job.get("source", "").startswith("http")
    if out["is_upload"]:
        out["source"] = os.path.basename(job["source"])
    out["queue_position"] = runner.queue_position(job["id"]) if runner and job.get("status") == "queued" else None
    out["thumbnail"] = None
    folder = job.get("output_dir")
    if folder and os.path.isfile(os.path.join(folder, "thumbnail_rank_1.jpg")):
        out["thumbnail"] = f"/media/{job['id']}/thumbnail_rank_1.jpg"
    return out


def _discover_cli_runs(known: set) -> list[dict]:
    """Earlier command-line runs in outputs/ shown as read-only projects."""
    out = []
    if not os.path.isdir(J.OUTPUTS_DIR):
        return out
    from clipping.results import summarize_render

    for name in sorted(os.listdir(J.OUTPUTS_DIR)):
        folder = os.path.join(J.OUTPUTS_DIR, name)
        meta = os.path.join(folder, "metadata_preview.json")
        if not _SAFE_NAME.match(name) or os.path.normcase(os.path.abspath(folder)) in known or not os.path.isfile(meta):
            continue
        manifest = J._read_json(os.path.join(folder, "render_manifest.json"))
        has_manifest = isinstance(manifest, list)
        manifest = _relocate(manifest if has_manifest else [], folder)
        ok, failed, _ = summarize_render(manifest)
        clips = J._read_json(meta) or []
        source = next((m.get("source_url") for m in manifest if isinstance(m, dict) and m.get("source_url")), "") or ""
        created = os.path.getmtime(meta)
        from datetime import datetime, timezone

        out.append({
            "id": f"cli-{name}", "title": (clips[0].get("title_inggris") if clips else None) or name,
            "source": source,
            "youtube_id": _youtube_id(source) or (name[:11] if re.match(r"^[A-Za-z0-9_-]{11}(?:_|$)", name) else None),
            "status": ("incomplete" if not has_manifest else
                       "success" if ok and not failed else ("partial" if ok else "failed")),
            "stage_label": ("Not finished (no render manifest)" if not has_manifest else
                            f"{ok} clips" + (f", {failed} failed" if failed else "")), "percent": 100,
            "rendered": ok, "failed": failed, "created_at": datetime.fromtimestamp(created, timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"), "readonly": True, "options": {}, "is_upload": False,
            "thumbnail": f"/media/cli-{name}/thumbnail_rank_1.jpg"
            if os.path.isfile(os.path.join(folder, "thumbnail_rank_1.jpg")) else None,
        })
    return out


def _relocate(manifest: list, folder: str) -> list[dict]:
    """Point manifest file paths at *folder* (runs may have been moved since rendering)."""
    out = []
    for row in manifest:
        if isinstance(row, dict):
            row = dict(row)
            for key in ("video_path", "thumbnail_path"):
                if row.get(key):
                    row[key] = os.path.join(folder, os.path.basename(row[key]))
            out.append(row)
    return out


def _clips_for(job_id: str, folder: str) -> list[dict]:
    meta = J._read_json(os.path.join(folder, "metadata_preview.json")) or []
    manifest = J._read_json(os.path.join(folder, "render_manifest.json")) or []
    by_rank = {m.get("rank"): m for m in manifest if isinstance(m, dict)}
    clips = []
    for c in meta if isinstance(meta, list) else []:
        rank = c.get("rank")
        m = by_rank.get(rank, {})
        video = os.path.basename(m.get("video_path") or f"highlight_rank_{rank}_ready.mp4")
        thumb = os.path.basename(m.get("thumbnail_path") or f"thumbnail_rank_{rank}.jpg")
        has_video = os.path.isfile(os.path.join(folder, video))
        k = c.get("klasifikasi_akun") or {}
        hook = c.get("hook") if isinstance(c.get("hook"), dict) else {}
        clips.append({
            "rank": rank,
            "status": "success" if has_video and m.get("status", "success") == "success" else (m.get("status") or "missing"),
            "error": m.get("error"),
            "video_url": f"/media/{job_id}/{video}" if has_video else None,
            "thumbnail_url": f"/media/{job_id}/{thumb}" if os.path.isfile(os.path.join(folder, thumb)) else None,
            "start_time": c.get("start_time"), "end_time": c.get("end_time"),
            "duration": m.get("duration") or round(float(c.get("end_time", 0)) - float(c.get("start_time", 0)), 1),
            "title": c.get("title_inggris") or c.get("title_indonesia") or f"Clip {rank}",
            "short_title": c.get("title_indonesia"),
            "hook": hook.get("text") or "",
            "reason": c.get("alasan") or "",
            "youtube_title": c.get("youtube_title_final") or c.get("title_inggris") or "",
            "youtube_description": c.get("youtube_description_final") or "",
            "youtube_tags": c.get("youtube_tags_final") or c.get("keyword_tags") or [],
            "hashtags": c.get("hastag") or "",
            "tiktok_caption": c.get("tiktok_caption_final") or c.get("tiktok_caption") or "",
            "tiktok_title": c.get("tiktok_title_id_final") or c.get("tiktok_title_id") or "",
            "viral_score": c.get("viral_score"), "ai_viral_score": c.get("ai_viral_score"),
            "score_breakdown": c.get("score_breakdown"), "score_components": c.get("score_components"),
            "visual_score": c.get("visual_score"), "visual_notes": c.get("visual_notes"),
            "audience_signals": c.get("audience_signals"),
            "account": k.get("tipe_akun"), "account_handle": k.get("akun_tujuan"),
            "loudness_normalized": m.get("loudness_normalized"),
        })
    return sorted(clips, key=lambda x: (x["rank"] is None, x["rank"]))


def _read_env() -> dict:
    env = {}
    try:
        with open(ENV_FILE, encoding="utf-8") as fh:
            for line in fh:
                if "=" in line and not line.lstrip().startswith("#"):
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def _write_env(updates: dict):
    """Set/replace KEY=value lines in .env, keeping everything else as-is."""
    lines, seen = [], set()
    try:
        with open(ENV_FILE, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        pass
    for i, line in enumerate(lines):
        key = line.partition("=")[0].strip()
        if key in updates and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={updates[key]}"
            seen.add(key)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    with open(ENV_FILE, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _real(value) -> bool:
    from clipping.config import is_placeholder_key

    return not is_placeholder_key(value)


def _mask(value) -> str | None:
    return f"…{value[-4:]}" if _real(value) and len(value) > 8 else None
