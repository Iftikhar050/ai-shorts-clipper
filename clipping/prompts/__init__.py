"""
clipping.prompts — Analysis prompt templates.

The main prompt lives in editable files next to this module:

    analysis.en.md   English prompt (default). Output language is configurable.
    analysis.id.md   Original Indonesian prompt (bilingual ID/EN metadata).

Templates use ``$name`` / ``${name}`` placeholders (Python ``string.Template``),
so JSON braces in the files need no escaping. Placeholders:

    clip_count, min_duration, max_duration, hook_duration, transcript,
    account_section, hook_v2_section, segment_section, signals_section,
    example_account, example_handle, alt_account, alt_handle,
    output_language (English template only)
"""

from pathlib import Path
from string import Template

PROMPT_DIR = Path(__file__).resolve().parent
SUPPORTED_LANGUAGES = ("en", "id")
DEFAULT_PROMPT_LANGUAGE = "en"
DEFAULT_OUTPUT_LANGUAGE = "English"

# Optional sections that are switched on by CLI flags. The Indonesian text is the
# upstream prompt verbatim; tests/fixtures/prompt_id_*.txt guard it byte-for-byte.
_SECTIONS = {
    "id": {
        "hook_v2": """

HOOK V2 (MULTI-HOOK INTRO — WAJIB):
- Selain hook standar, buat juga "hook_v2" berisi $items potongan pendek (0.5-2 detik) yang diambil dari momen paling mencolok/controversial/emosional di dalam klip.
- Gaya: $style
- Setiap item harus berisi: start_time, end_time, dan text (teks on-screen singkat 2-5 kata).
- Item harus diurutkan dari paling kuat ke paling lemah.
- Transisi antar item akan ditambahkan otomatis (white flash / glitch) oleh sistem.
- Isi field "hook_v2" sebagai objek dengan:
  - "enabled": true
  - "items": array dari objek (start_time, end_time, text)
  - "transition": objek dengan "type" ("white_flash" atau "glitch")
""",
        "segment": """

SEGMENT-BASED TRIMMING (KEEP SEGMENTS — WAJIB):
- Untuk setiap klip, analisis apakah ada bagian yang kurang menarik, terlalu diam, bertele-tele, atau filler di tengah.
- Jika ada, pecah klip menjadi beberapa "keep_segments" — hanya potongan terbaik yang dipertahankan.
- Setiap segment berisi: start_time dan end_time.
- Segment harus berurutan secara kronologis dan tidak boleh overlap.
- Jika seluruh durasi klip sudah padat dan menarik, cukup buat 1 segment yang mencakup seluruh durasi.$silence_hint
- Isi field "keep_segments" sebagai array dari objek (start_time, end_time).
""",
        "silence_hint": "\n- AGRESIF buang bagian diam/silence/dead air. Jangan sertakan jeda lebih dari 0.5 detik.",
        "account_general": (
            "KLASIFIKASI AKUN TUJUAN:\n"
            'Untuk setiap klip isi klasifikasi_akun dengan tipe_akun "General" dan akun_tujuan "General" '
            "(alternatif_akun juga General). Tidak perlu analisis akun."
        ),
        "account_header": (
            "KLASIFIKASI AKUN TUJUAN (UNTUK SETIAP KLIP):\n"
            "Tentukan akun tujuan berdasarkan ANGLE video dari klip tersebut, bukan hanya topiknya. "
            "tipe_akun HARUS salah satu dari nama akun di bawah ini, dan akun_tujuan HARUS handle akun tersebut:\n"
        ),
        "account_rules_header": "\n\nATURAN KHUSUS KLASIFIKASI:\n",
        "signals": """

SINYAL AUDIENS (DATA PENONTON ASLI):
- Beberapa baris transkrip diakhiri penanda [AUDIENCE: ...] dari data penonton YouTube: "replayed N%" = seberapa sering penonton mengulang momen itu (100% = bagian paling sering diulang); "N viewer comments point here" = komentar yang menyebut timestamp tersebut.
- Baris yang diawali [CHAPTER at ...] adalah chapter dari uploader; titik awal/akhir yang natural.
- Anggap momen yang sering diulang atau disebut di komentar sebagai minat penonton yang terbukti: utamakan momen ini selama klip tetap mengikuti semua aturan di atas.
- Penanda BUKAN ucapan: jangan pernah dipakai sebagai hook, kata typography, judul, atau caption.
""",
    },
    "en": {
        "hook_v2": """

HOOK V2 (MULTI-HOOK INTRO — REQUIRED):
- In addition to the standard hook, create "hook_v2" with $items short cuts (0.5-2 seconds each) taken from the most striking, controversial or emotional moments inside the clip.
- Style: $style
- Each item must contain: start_time, end_time and text (short on-screen text, 2-5 words).
- Order items from strongest to weakest.
- Transitions between items (white flash / glitch) are added automatically by the system.
- Fill "hook_v2" as an object with:
  - "enabled": true
  - "items": array of objects (start_time, end_time, text)
  - "transition": object with "type" ("white_flash" or "glitch")
""",
        "segment": """

SEGMENT-BASED TRIMMING (KEEP SEGMENTS — REQUIRED):
- For each clip, check whether any part in the middle is weak, too quiet, rambling or filler.
- If so, split the clip into several "keep_segments" — keep only the best parts.
- Each segment contains: start_time and end_time.
- Segments must be in chronological order and must not overlap.
- If the whole clip is already tight and engaging, return 1 segment covering the full clip.$silence_hint
- Fill "keep_segments" as an array of objects (start_time, end_time).
""",
        "silence_hint": "\n- AGGRESSIVELY remove silence and dead air. Do not keep pauses longer than 0.5 seconds.",
        "account_general": (
            "TARGET ACCOUNT CLASSIFICATION:\n"
            'For every clip, fill klasifikasi_akun with tipe_akun "General" and akun_tujuan "General" '
            "(alternatif_akun is also General). No account analysis is needed."
        ),
        "account_header": (
            "TARGET ACCOUNT CLASSIFICATION (FOR EVERY CLIP):\n"
            "Choose the target account based on the ANGLE of the clip, not just its topic. "
            "tipe_akun MUST be one of the account names below, and akun_tujuan MUST be that account's handle:\n"
        ),
        "account_rules_header": "\n\nCLASSIFICATION TIE-BREAK RULES:\n",
        "signals": """

AUDIENCE SIGNALS (REAL VIEWER DATA):
- Some transcript lines end with [AUDIENCE: ...] markers from YouTube's real viewer data: "replayed N%" = how often viewers rewatched that moment (100% = the most replayed part of the video); "N viewer comments point here" = comments that link to that timestamp.
- Lines starting with [CHAPTER at ...] are the uploader's chapters; they are natural start/end points.
- Treat strongly replayed or comment-linked moments as proven audience interest: prefer them when choosing clips, as long as the clip still follows every rule above.
- Markers are NOT speech: never use them as hook text, typography words, titles or captions.
""",
    },
}


def resolve_language(cfg) -> str:
    """Prompt language for *cfg* (``en`` when unset)."""
    lang = getattr(cfg, "prompt_language", None) or DEFAULT_PROMPT_LANGUAGE
    if lang not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Unsupported prompt language {lang!r}; choose one of {SUPPORTED_LANGUAGES}.")
    return lang


def section(lang: str, name: str, **values) -> str:
    return Template(_SECTIONS[lang][name]).substitute(**values)


def load_template(lang: str) -> Template:
    path = PROMPT_DIR / f"analysis.{lang}.md"
    return Template(path.read_text(encoding="utf-8"))


def render_analysis_prompt(lang: str, **values) -> str:
    """Fill the analysis template. Unknown ``$words`` in edited files are left as-is."""
    return load_template(lang).safe_substitute(**values)
