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
2. **Combined score.** When a heatmap or comment timestamps exist, the AI is
   asked for extra candidates (2x, at most +5), then each is scored:

   ```
   final = (0.5 × AI + 0.3 × heatmap peak + 0.2 × comments) / (sum of weights used)
   ```

   Only signals available for that video count. Candidates overlapping more
   than 50% with a better clip are skipped. The top `--clips` are rendered.

Every clip keeps the details in `metadata_preview.json`:

```json
"viral_score": 95,
"ai_viral_score": 92,
"score_breakdown": {"hook": 19, "emotion": 18, "shareability": 18, "clarity": 18, "payoff": 19},
"score_components": {"ai": 0.92, "heatmap": 1.0},
"audience_signals": {"heatmap_peak": 1.0, "comment_mentions": null}
```

The raw signals are saved to `outputs/audience_signals.json`.

## Flags

| Flag | Default | Meaning |
|---|---|---|
| `--no-audience-signals` | off | Ignore heatmap/chapters/comments |
| `--use-comments` | off | Fetch top comments for timestamps |
| `--max-comments N` | 300 | Comment limit |
| `--score-weights` | `ai=0.5,heatmap=0.3,comments=0.2` | Combined-score weights |
| `--ai-temperature` | 0.3 | Gemini sampling temperature |

Comment fetching is optional because it adds a request to YouTube and can hit
rate limits. If it fails, the run continues without comments.
