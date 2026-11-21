# Clip scoring

`viral_score` used to be a single number from the AI with no explanation.
It now has two transparent layers.

## 1. Score breakdown (English prompt)

The AI reports five 1-20 component scores in `score_breakdown`, and
`viral_score` is their sum:

| Component | Question |
|---|---|
| `hook` | Do the first 3 seconds stop the scroll? |
| `emotion` | How strong is the emotion / conflict / humor? |
| `shareability` | Will people comment, share, tag or save? |
| `clarity` | Does it make sense without the full video? |
| `payoff` | Is there a reward for watching to the end? |

Gemini runs at `--ai-temperature 0.3` (default) so picks and scores are more
consistent between runs. `--prompt-language id` keeps the original contract
(no breakdown).

## 2. Audience signals (YouTube)

Real viewer data, collected without extra AI cost:

| Signal | Source | Availability |
|---|---|---|
| Heatmap ("Most replayed") | Video info from the download | Only some videos, mostly very popular ones |
| Chapters | Video info from the download | When the uploader added chapters |
| Comment timestamps (`3:15 😂`) | `--use-comments` (one extra request) | Depends on the audience |

Signals are used in two ways:

1. **Transcript markers** the AI can see:
   ```
   [CHAPTER at 60.0s] The big moment
   [62.0 - 68.0] the big moment [AUDIENCE: replayed 100%, 2 viewer comments point here]
   ```
2. **Combined score.** When usable signals exist, the AI is asked for extra
   candidates (2x, at most +5), then each is scored:

   ```
   final = (0.5 × AI + 0.3 × heatmap + 0.2 × comments + 0.1 × audio) / (sum of weights used)
   ```

   A signal only counts when it can tell candidates apart:
   - comments need at least 5 timestamps (1-2 stray timestamps are ignored)
   - a signal that is 0 for every candidate is dropped, so it can't drag all
     scores down

   Candidates overlapping more than 50% with a better clip are skipped. The
   top `--clips` are rendered.

## 3. Video events (FFmpeg, every source)

Detected from the video file itself, on CPU, with no AI cost:

| Event | How | Typical cost (11-min 1080p) |
|---|---|---|
| Scene-cut bursts | ≥3 hard cuts within 6 s on a 4 fps, 256 px copy (replays, montages) | ~10-40 s |
| Volume spikes | A second ≥6 dB above its surrounding 30 s | ~2 s |

They appear as `[EVENT: 3 rapid scene cuts]` / `[EVENT: volume spike +9 dB]`
markers and as the `audio` score component.

What to expect, measured on real uploads: most YouTube audio is
loudness-compressed (about 2-3 dB of range), so volume spikes rarely fire on
it. That is deliberate, since a lower threshold mostly marks noise. Spikes
help on less-processed audio (live streams, gameplay, raw podcasts). Scene-cut
bursts do work on edited content: on a cricket highlights video the detected
burst fell inside the AI's #1 pick (the century replay). A talking-head video
correctly produced no events.

Every clip keeps the details in `metadata_preview.json`:

```json
"viral_score": 95,
"ai_viral_score": 92,
"score_breakdown": {"hook": 19, "emotion": 18, "shareability": 18, "clarity": 18, "payoff": 19},
"score_components": {"ai": 0.92, "heatmap": 1.0},
"audience_signals": {"heatmap_peak": 1.0, "comment_mentions": null}
```

The raw signals are saved to `outputs/audience_signals.json`.

## 4. Visual review (`--visual-rerank`, Gemini only)

The clip picker only reads the transcript. With `--visual-rerank`, the AI is
asked for extra candidates, then FFmpeg grabs `--visual-frames` (default 6)
small frames (512 px) spread across each candidate, and ONE extra Gemini
request scores what it **sees** (0-100):

- visual hook in the first frames
- visible action or emotion (celebrations, reactions, gestures)
- variety versus one static shot
- a clear subject that crops well to vertical 9:16
- penalties for static slides, black frames, logos, empty scenes

This becomes the `visual` score component (weight 0.3), and each clip keeps
`visual_score` and `visual_notes`. Example from a cricket highlights video:

| Candidate | AI | Visual | Gemini's note |
|---|---|---|---|
| Intro | 88 | 48 | Wide aerial view; distant shots are hard to crop to 9:16 |
| Century | 91 | 78 | Great celebration hook and close player shots, some dark angles |
| Bowling spell | 85 | 88 | Dynamic action with tight framing, ideal for vertical |

It is opt-in because it adds images to the Gemini request (about 6 frames x
6 candidates for 3 clips). It is skipped with `--load-gemini-json` or
non-Gemini providers, and if it fails the run continues with the normal
ranking.

## Flags

| Flag | Default | Meaning |
|---|---|---|
| `--no-audience-signals` | off | Ignore heatmap/chapters/comments |
| `--no-event-markers` | off | Skip FFmpeg event detection |
| `--no-scene-detect` | off | Keep volume spikes, skip the scene-cut pass |
| `--visual-rerank` | off | Gemini judges sampled frames of each candidate |
| `--visual-frames N` | 6 | Frames per candidate (1-12) |
| `--use-comments` | off | Fetch top comments for timestamps |
| `--max-comments N` | 300 | Comment limit |
| `--score-weights` | `ai=0.5,heatmap=0.3,comments=0.2,audio=0.1,visual=0.3` | Combined-score weights |
| `--ai-temperature` | 0.3 | Gemini sampling temperature |

Comment fetching is optional because it adds a request to YouTube and can hit
rate limits. If it fails, the run continues without comments.
