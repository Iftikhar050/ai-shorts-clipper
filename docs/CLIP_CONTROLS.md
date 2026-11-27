# Clip controls & run results

## Choosing what gets clipped

| Flag | Default | What it does |
|---|---|---|
| `--clip-length short\|medium\|long\|any` | `medium` | Target length: short 15-30 s, medium 30-60 s, long 60-90 s, any 20-179 s (original behaviour) |
| `--min-duration N` / `--max-duration N` | preset | Override either end of the range |
| `--topic "..."` | none | Only clip moments about this topic, e.g. `--topic "sleep and dopamine"`. Fewer clips are returned rather than off-topic ones. |
| `--time-range 2:00-10:30` | whole video | Only clip from this part of the video (seconds, mm:ss or hh:mm:ss) |

The AI is told the length range, and the result is checked afterwards:
candidates more than 5 s outside the range are dropped, as long as enough
in-range candidates remain (you always get clips back). The hook teaser adds a
few seconds on top of the clip length.

## Finishing

| Flag | Default | What it does |
|---|---|---|
| `--no-loudnorm` | normalize | Finished clips are normalized to -14 LUFS (the level YouTube, TikTok and Instagram play at). Only the audio is re-encoded; the picture is copied. |
| `--render-retries N` | 1 | Retry a clip whose render failed (0-3) |

## Where clips go

Each video gets its own folder: `outputs/<video-id>/` with the clips,
thumbnails, `metadata_preview.json`, `render_manifest.json` and the source
video. Running the same video again reuses (and overwrites) its folder; other
videos are never touched. `--flat-output` restores the old behaviour of
writing straight into `outputs/`. Batch runs (several `--url`s) already used
per-video folders.

## Reading the result

Every run ends with a per-clip summary:

```
⚠️ Selesai sebagian: 2 berhasil, 1 GAGAL.
   ✅ #1  42.3s  outputs/8MXhRmQ4kR0/highlight_rank_1_ready.mp4
   ✅ #2  38.0s  outputs/8MXhRmQ4kR0/highlight_rank_2_ready.mp4
   ❌ #3  FAILED: Error opening output file h_3.ts.
```

The exit code is `0` only when every clip rendered (status `success` and the
file exists), and `1` otherwise, so scripts and schedulers can detect
failures. Batch runs mark each video `success`, `partial` or `failed`.

## Resilience

- Gemini "high demand" / quota errors (503/429): after 2 in a row the
  fallback model is used instead of waiting through up to 10 retries; the
  fallback itself gets 3 attempts.
- API keys still set to the `.env.sample` placeholders count as missing, so
  optional features (Pexels B-roll) are skipped cleanly instead of failing
  with 401 errors.
- Hugging Face model downloads use classic HTTP (`HF_HUB_DISABLE_XET=1`),
  since the Xet transfer stalled on Windows. Set `HF_HUB_DISABLE_XET=0` to
  opt back in.
