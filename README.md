# AI Shorts Clipper

**Turn long videos and podcasts into vertical Shorts, Reels and TikToks, on an ordinary laptop with no GPU.**

AI Shorts Clipper finds the strongest moments in a long video, cuts them to the length you want, frames the speaker vertically, burns in word-by-word captions, and writes the titles, descriptions and hashtags for every platform. It runs entirely on your own computer, from a simple web app or the command line.

- **CPU-first:** built and tested on a Windows laptop (Intel i5, 16 GB RAM, no graphics card)
- **Explains its choices:** every clip comes with a score breakdown and the reason it was picked
- **Learns from your channel:** compares its predictions with your real YouTube results over time
- **Any language for titles and captions:** English by default, Urdu, Spanish and others on request

---

## Features

| | |
|---|---|
| **Smart clip selection** | Gemini reads the transcript and picks self-contained moments with a strong hook, emotion and payoff |
| **Score you can read** | Hook, emotion, shareability, clarity and payoff (each /20) for every clip |
| **Sees the video** | Optional visual review: Gemini looks at sampled frames to judge action, faces and how well a shot crops to 9:16 |
| **Real audience signals** | YouTube "Most replayed" heatmap, chapters and timestamped comments guide and re-rank clips |
| **Video events** | Scene-cut bursts (replays, montages) and volume spikes detected with FFmpeg |
| **You decide what to clip** | Length presets (15–30 s, 30–60 s, 60–90 s), a topic ("only clips about sleep"), or a time range |
| **Vertical framing** | MediaPipe face tracking keeps the speaker centered in 9:16, 1:1, 4:5 or 16:9 |
| **Captions** | Karaoke-style word highlighting with several caption styles |
| **Hook teaser** | Each clip opens with its strongest line |
| **Even loudness** | Every clip normalized to −14 LUFS, the level YouTube, TikTok and Instagram play at |
| **Ready-to-post text** | YouTube title, description and tags, TikTok caption and hashtags, in the language you choose |
| **Your accounts** | Clips are routed to the right channel (e.g. Sports, Science) based on their angle |
| **Reliable runs** | Honest pass/fail per clip, automatic retry of failed renders, fallback when the AI is overloaded |
| **Performance tracking** | Pull views and retention for posted clips and see which predictions actually worked |

Sources: YouTube, TikTok, Instagram, Google Drive links, or a video file from your computer.

---

## Quick start (Windows)

### 1. Install the basics
- **Python 3.12** from [python.org](https://www.python.org/downloads/) (3.12 specifically; some video libraries don't support newer versions yet)
- **FFmpeg**, available on your PATH ([download](https://ffmpeg.org/download.html))
- A free **Gemini API key** from [Google AI Studio](https://aistudio.google.com/apikey)

### 2. Set up the project
```powershell
git clone https://github.com/Iftikhar050/ai-shorts-clipper.git
cd ai-shorts-clipper
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-cpu.txt
copy .env.sample .env
```
Open `.env` and set `GOOGLE_API_KEY=` to your key, or add it later in the app's **Settings** page.

### 3. Start the app
Double-click **`Start Shorts Clipper.bat`**, or run:
```powershell
python run_app.py
```
Your browser opens at **http://127.0.0.1:8765**. Paste a link (or drop a video), choose how many clips and how long, and press **Get clips**. Keep the app window open while you use it.

The first run downloads the Whisper speech model (about 460 MB) once.

---

## The web app

| Page | What you can do |
|---|---|
| **Projects** | Paste a link or drag in a video; choose clips, length, format and caption style; optional topic, time range, language, visual review and more. All your projects, with live status. |
| **Project** | Live progress (getting video → transcribing → finding moments → rendering clip 2 of 3), the log, then every clip: play, download, and **Details** with the score breakdown, why it was picked, and copy buttons for every title and caption. |
| **Accounts** | Describe your channels; the AI routes each clip to the best fit. |
| **Settings** | API keys (stored locally, never shown again in full), models and quality. |

It runs only on your computer (`127.0.0.1`) and has no login, so don't expose it to a network. More in [docs/APP.md](docs/APP.md).

---

## Command line

Everything the app does is also available from the terminal:

```powershell
.\.venv\Scripts\python.exe main.py --url "https://www.youtube.com/watch?v=VIDEO_ID" --clips 3
```

Useful options:

| Option | Example | What it does |
|---|---|---|
| `--clips` | `--clips 5` | Number of clips |
| `--clip-length` | `--clip-length short` | `short` 15–30 s, `medium` 30–60 s (default), `long` 60–90 s, `any` |
| `--topic` | `--topic "sleep and dopamine"` | Only clip moments about this topic |
| `--time-range` | `--time-range 2:00-10:30` | Only clip from this part of the video |
| `--ratio` | `--ratio 1:1` | `9:16` (default), `1:1`, `4:5`, `16:9`, `3:4` |
| `--font-style` | `--font-style HORMOZI` | Caption style: `DEFAULT`, `HORMOZI`, `STORYTELLER`, `CINEMATIC` |
| `--output-language` | `--output-language Urdu` | Language for titles, captions and hashtags |
| `--visual-rerank` | | Let Gemini look at frames before ranking (uses more AI) |
| `--use-comments` | | Use timestamps viewers mention in YouTube comments |
| `--accounts-file` | `--accounts-file accounts.json` | Your channels (default: `accounts.json` if present) |
| `--whisper-model` | `--whisper-model small` | Speech model; `small` with `--whisper-device cpu --whisper-compute-type int8` suits laptops |
| `--output-dir` | `--output-dir D:\clips\ep12` | Where to save (default: `outputs\<video-id>\`) |

Run `python main.py --help` for the full list. A run exits with code `0` only when every clip rendered, so it works in scripts and schedulers.

Each run saves to its own folder: the clips (`highlight_rank_N_ready.mp4`), thumbnails, `metadata_preview.json` (all titles, captions and scores) and `render_manifest.json`.

---

## How it works

```
 link / file
   │
   ▼  1. Get the video ............ yt-dlp, or copy a local file
   ▼  2. Transcribe ............... YouTube subtitles, otherwise Whisper on your CPU
   ▼  3. Read signals ............. Most-replayed heatmap · chapters · comments · scene cuts · volume spikes
   ▼  4. Find the moments ......... Gemini picks candidates with hooks, captions plan, titles and a score breakdown
   ▼  5. Re-rank (optional) ....... visual review of sampled frames + combined score, near-duplicates removed
   ▼  6. Render each clip ......... face-tracked crop · karaoke captions · hook teaser · −14 LUFS audio
   ▼  7. Publish & learn .......... YouTube upload · performance tracking feeds back into future picks
```

Details:

| Topic | Doc |
|---|---|
| Clip length, topic, time range, output folders, exit codes | [docs/CLIP_CONTROLS.md](docs/CLIP_CONTROLS.md) |
| Scoring, audience signals, events, visual review | [docs/SCORING.md](docs/SCORING.md) |
| Prompt templates and languages | [docs/PROMPTS.md](docs/PROMPTS.md) |
| Target accounts | [docs/ACCOUNTS.md](docs/ACCOUNTS.md) |
| Tracking real performance | [docs/PERFORMANCE.md](docs/PERFORMANCE.md) |
| Web app | [docs/APP.md](docs/APP.md) |

---

## Speed on a laptop

Measured on an Intel i5 laptop with 16 GB RAM and no GPU:

| Video | Result | Time |
|---|---|---|
| 8-minute sports highlights, 3 clips, visual review on | 3 clips of 36–53 s | 19 min |
| 3-minute talk (uploaded file), 1 clip, topic set | 1 clip of 21 s | 4 min |

Transcription and rendering run on the CPU. When YouTube subtitles are available, transcription is skipped and runs finish sooner. A GPU speeds up both steps a lot (`--whisper-device cuda`).

---

## Use it responsibly

Clip **your own content** or content you have permission to use. Downloading and re-uploading other creators' videos can break platform rules and copyright law. You are responsible for what you publish.

---

## Project layout

```
main.py                 command-line entry point
run_app.py              web app launcher (Start Shorts Clipper.bat)
run_performance.py      performance tracking
run_upload.py           YouTube uploader
app/                    web app (server, job queue, static front end)
clipping/               pipeline: download, transcribe, analyse, score, render
  prompts/              editable AI prompt templates
docs/                   guides
tests/                  automated tests (pytest)
```

Run the tests with:
```powershell
.\.venv\Scripts\python.exe -m pytest tests
```

---

## Credits and license

Maintained by [Iftikhar050](https://github.com/Iftikhar050).

AI Shorts Clipper started from [opensource-clipping](https://github.com/NaufalRizqullah/opensource-clipping) by Muhammad Naufal Rizqullah and has since been substantially rewritten and extended. Released under the [MIT License](LICENSE).
