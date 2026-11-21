# Learning from real performance

The clipper *predicts* which moments will do well. `run_performance.py`
checks those predictions against how your uploaded clips actually perform,
then feeds what it learns back into the next runs.

```
render clips ─► upload ─► fetch real stats ─► report ─► better weights + prompt examples ─┐
     ▲                                                                                      │
     └──────────────────────────────────────────────────────────────────────────────────────┘
```

## One-time setup (about 10 minutes)

1. In [Google Cloud Console](https://console.cloud.google.com/), create (or reuse) a project.
2. **APIs & Services → Library**: enable **YouTube Data API v3** and **YouTube Analytics API**.
3. **APIs & Services → OAuth consent screen**: set it up (External, add yourself as a test user).
4. **Credentials → Create credentials → OAuth client ID → Desktop app**. Download the JSON,
   rename it `client_secret.json` and put it in `.credentials/` (the uploader uses the same file).
5. Sign in once:
   ```
   .\.venv\Scripts\python.exe run_performance.py auth
   ```
   A browser opens; choose the account that owns your channel. This saves
   `.credentials/youtube_analytics_token.json` with **read-only** access
   (`youtube.readonly`, `yt-analytics.readonly`). It is separate from the uploader's token.

`.credentials/`, `performance.sqlite3` and `clipping/prompts/top_examples.json`
are git-ignored. Treat the token like a password.

## Routine

| When | Command | What it does |
|---|---|---|
| After each run | `run_performance.py ingest outputs\<run>` | Stores each clip's predictions. If you used `run_upload.py`, video IDs are linked automatically from `render_manifest_uploaded.json`. |
| After a manual upload | `run_performance.py link outputs\<run> <rank> <VIDEO_ID>` | Links a clip to its YouTube video (the ID is the part after `/shorts/`). |
| Daily (or weekly) | `run_performance.py fetch` | Snapshots views, likes, comments, average view duration and % viewed. |
| Every ~10 new uploads | `run_performance.py report` | Shows which predictions track real results. |
| When you like the results | `run_performance.py examples` | Writes your top 3 clips to `clipping/prompts/top_examples.json`; the English prompt then shows them to the AI as references (`--no-examples` turns this off). |

Keep each run in its own `outputs\<name>` folder (the clipper overwrites
`outputs\` on the next run), and ingest it before the next run.

## Reading the report

```
Which predictions track real views?  (Spearman: +1 perfect, 0 none, -1 inverse)
  breakdown.hook               +0.62  n=24  +############
  component.visual             +0.41  n=24  +########
  ai_viral_score               +0.18  n=24  +####
  breakdown.clarity            -0.05  n=24  -#
  Suggested --score-weights (from views): ai=0.30,visual=0.70
```

- Each line is a rank correlation between a prediction and real views (or
  average % viewed): does a higher prediction mean a better result?
- Below 5 clips there is no report; below 20 it is labelled "early hints".
  Correlations on a handful of clips swing a lot, so don't retune on day one.
- Only snapshots at least 72 hours after publishing are used by default
  (`--min-age-hours`), since Shorts views change a lot in the first days.
- The suggested `--score-weights` are proportional to each component's
  positive correlation (AI kept at least 0.1). They are a starting point:
  try them on your next runs and compare.

## Limits

- Retention metrics (average view duration and %) come from YouTube Analytics
  and only work for **your own channel**. They can take a day or two to appear
  for new uploads; until then those columns are empty.
- TikTok and Instagram are not tracked (no comparable public analytics API).
- API quota: `fetch` uses one Data API call per 50 videos plus one Analytics
  call per video. Daily quotas are set by Google [NEEDS VERIFICATION for your project].
