# Analysis prompt & languages

The AI analysis prompt is a template file you can edit without touching Python:

| File | Used when |
|---|---|
| `clipping/prompts/analysis.en.md` | `--prompt-language en` (default) |
| `clipping/prompts/analysis.id.md` | `--prompt-language id` (original Indonesian prompt) |

Smaller optional sections (Hook V2, segment trimming, account classification)
are in `clipping/prompts/__init__.py`, in both languages.

## Flags

| Flag | Default | Meaning |
|---|---|---|
| `--prompt-language en\|id` | `en` | Which template to use |
| `--output-language NAME` | `English` | Language for titles, captions, hashtags, reasons (English template only), e.g. `Urdu`, `Spanish` |
| `--sub-langs LIST` | `en,id` | YouTube subtitle languages to try, in order, with `--use-dlp-subs` |

With `--prompt-language id` the output stays bilingual as before (Indonesian
`title_indonesia` / `tiktok_*_id`, English platform fields) and
`--output-language` is ignored.

## Field names

JSON field names are unchanged (`title_indonesia`, `title_inggris`,
`tiktok_title_id`, `alasan`, ...) because the renderer, metadata and uploaders
use them. With the English template they hold text in your output language:

| Field | Meaning in the English template |
|---|---|
| `title_indonesia` | Short title, also the **thumbnail headline** |
| `title_inggris` | Main platform title (YouTube) |
| `tiktok_title_id` | Longer, descriptive TikTok title |
| `tiktok_caption_id` | Alternative TikTok caption |

## Editing the template

Placeholders use `${name}`: `clip_count`, `min_duration`, `max_duration`,
`hook_duration`, `account_section`, `hook_v2_section`, `segment_section`,
`example_account`, `example_handle`, `alt_account`, `alt_handle`,
`output_language`, `transcript`. JSON braces need no escaping. Keep the
"REQUIRED JSON STRUCTURE" block intact, since the output is parsed by field
name.

`tests/test_prompts.py` freezes the Indonesian prompt byte-for-byte
(`tests/fixtures/prompt_id_*.txt`), so upstream behaviour can't drift by accident.
