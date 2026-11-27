# Web app

A local interface for the clipper: paste a link (or drop a video), pick a few
options, watch progress, then preview, download and copy captions for every
clip.

```
.\.venv\Scripts\python.exe run_app.py
```

It opens <http://127.0.0.1:8765>. Use `--port 9000` to change the port and
`--no-browser` to skip opening a tab. Stop it with Ctrl+C.

The app listens on **127.0.0.1 only** (this computer). It has no login, so
don't expose it to a network.

## Pages

| Page | What it does |
|---|---|
| **Projects** | Paste a YouTube / TikTok / Instagram / Google Drive link or upload a video (button or drag & drop anywhere). Options: number of clips, length (15–30 s, 30–60 s, 60–90 s, any), format (9:16, 1:1, 4:5, 16:9), caption style. **More options**: topic, time range, titles & captions language, words per caption line, visual review, viewer comments, karaoke, hook teaser, B-roll (needs a Pexels key), even loudness. Options are remembered in your browser. Below: every project, including earlier command-line runs from `outputs/` (read-only). |
| **Project** | Live progress (Get video → Transcribe → Signals → Find moments → Visual review → Render clip N of M), the log, errors in plain words, then the clips: play, download, and **Details** with the score breakdown, why the AI picked it, signals, and copy buttons for the YouTube title/description/tags, TikTok caption and hashtags. Cancel, run again, or delete (optionally with files). |
| **Accounts** | Edit your target accounts and tie-break rules (saved to `accounts.json`, validated before saving). |
| **Settings** | Set the Gemini and Pexels keys (saved to `.env`, never shown again in full), models, Whisper size/device and download quality. Shows whether FFmpeg was found. |

## How it works

- Each project runs the real CLI (`main.py`) in a background process, so the
  web app and the command line always behave the same.
- Jobs run **one at a time** (rendering uses the whole CPU); others wait in a
  queue with their position shown.
- Every job writes to `outputs/<job-id>/`. Uploads are stored in
  `app/data/uploads/`; job history and logs in `app/data/` (git-ignored).
- If the app is closed mid-job, that job is marked as interrupted on the next
  start and queued jobs resume.
- A job is **Done** only when every clip rendered, **Partly done** when some
  failed, and **Failed** with the most relevant error line otherwise.

## Files

| Path | Purpose |
|---|---|
| `run_app.py` | Starts the server |
| `app/server.py` | API routes, media streaming, settings, accounts |
| `app/jobs.py` | Job queue, option validation, CLI command, progress parsing |
| `app/static/` | `index.html`, `app.css`, `app.js` (no build step) |
| `tests/test_app.py` | API and job lifecycle tests |
