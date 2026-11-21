"""Analysis prompt templates: Indonesian output is frozen, English is the default."""

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from clipping import prompts
from clipping.accounts import load_accounts

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
TRANSCRIPT = "[0.0 - 4.2] first line\n[4.2 - 9.9] second line with {braces} and $dollar"


@pytest.fixture
def engine(monkeypatch):
    for name, symbol in (("yt_dlp", "YoutubeDL"), ("faster_whisper", "WhisperModel")):
        stub = ModuleType(name)
        setattr(stub, symbol, object)
        monkeypatch.setitem(sys.modules, name, stub)
    spec = importlib.util.spec_from_file_location("_prompts_engine", ROOT / "clipping" / "engine.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cases(lang):
    accounts, rules = load_accounts(ROOT / "accounts.example.json")
    return {
        "none": None,
        "full": SimpleNamespace(hook_v2=True, hook_v2_items=4, hook_v2_style="fast_glitch",
                                no_segment_trim=False, silence_trim=True, prompt_language=lang,
                                target_accounts=accounts, account_rules=rules, account_classify=True),
        "minimal": SimpleNamespace(hook_v2=False, no_segment_trim=True, silence_trim=False, prompt_language=lang,
                                   target_accounts=accounts, account_rules=[], account_classify=False),
    }


@pytest.mark.parametrize("case", ["none", "full", "minimal"])
def test_indonesian_prompt_is_byte_identical_to_pre_template_output(engine, case):
    cfg = _cases("id")[case]
    actual = engine.get_analysis_prompt(TRANSCRIPT, 3, 4, cfg=cfg, prompt_language="id")
    expected = (FIXTURES / f"prompt_id_{case}.txt").read_text(encoding="utf-8")
    assert actual == expected


@pytest.mark.parametrize("case", ["none", "full", "minimal"])
def test_english_prompt_is_default_and_fully_rendered(engine, case):
    cfg = _cases("en")[case]
    prompt = engine.get_analysis_prompt(TRANSCRIPT, 3, 4, cfg=cfg)
    assert prompt.startswith("\nYou are an Art Director")
    assert "Kamu adalah" not in prompt and "WAJIB" not in prompt
    assert "OUTPUT LANGUAGE: English." in prompt
    assert "${" not in prompt
    assert "Find the 3 most interesting" in prompt and "20-179 seconds" in prompt
    assert prompt.rstrip().endswith(TRANSCRIPT)  # $ and braces in the transcript survive


def test_english_optional_sections_follow_flags(engine):
    full = engine.get_analysis_prompt(TRANSCRIPT, 3, 4, cfg=_cases("en")["full"])
    assert "HOOK V2 (MULTI-HOOK INTRO — REQUIRED)" in full and "with 4 short cuts" in full
    assert "Style: fast_glitch" in full
    assert "SEGMENT-BASED TRIMMING" in full and "AGGRESSIVELY remove silence" in full
    assert "- Sports: Match highlights" in full and "CLASSIFICATION TIE-BREAK RULES:" in full
    assert '"tipe_akun": "Sports"' in full and '"tipe_akun": "Science"' in full

    minimal = engine.get_analysis_prompt(TRANSCRIPT, 3, 4, cfg=_cases("en")["minimal"])
    assert "HOOK V2" not in minimal and "SEGMENT-BASED TRIMMING" not in minimal
    assert 'tipe_akun "General"' in minimal


def test_output_language_is_configurable(engine):
    cfg = SimpleNamespace(output_language="Urdu")
    prompt = engine.get_analysis_prompt(TRANSCRIPT, 2, 3, cfg=cfg)
    assert "OUTPUT LANGUAGE: Urdu." in prompt
    assert "Write ALL text fields in Urdu" in prompt


def test_templates_share_the_same_placeholders():
    def names(lang):
        text = (prompts.PROMPT_DIR / f"analysis.{lang}.md").read_text(encoding="utf-8")
        return set(re.findall(r"\$\{?(\w+)\}?", text))

    assert names("en") - {"output_language"} == names("id")


def test_cli_language_flags(tmp_path, monkeypatch):
    from clipping.config import build_config

    monkeypatch.chdir(tmp_path)
    cfg = build_config(["--url", "https://youtube.com/watch?v=x"])
    assert (cfg.prompt_language, cfg.output_language, cfg.sub_langs) == ("en", "English", ["en", "id"])
    cfg = build_config(["--url", "https://youtube.com/watch?v=x", "--prompt-language", "id",
                        "--output-language", " Urdu ", "--sub-langs", "ur, en"])
    assert (cfg.prompt_language, cfg.output_language, cfg.sub_langs) == ("id", "Urdu", ["ur", "en"])
    with pytest.raises(SystemExit):
        build_config(["--url", "https://youtube.com/watch?v=x", "--prompt-language", "fr"])


def test_metadata_language_warnings_only_for_indonesian_prompt():
    from clipping.metadata import normalize_and_validate

    clip = {"rank": 1, "viral_score": 90, "start_time": 0, "end_time": 30,
            "title_indonesia": "Short title", "title_inggris": "Main title",
            "hastag": "#a #b", "description_hook": "Hook sentence here.",
            "description_context": "Context sentence for the clip goes here.",
            "keyword_tags": ["a", "b", "c", "d", "e"], "tiktok_title_id": "A longer English title",
            "tiktok_caption_id": "An alternative English caption", "tiktok_caption": "Caption"}
    en_report, id_report = [], []
    normalize_and_validate([dict(clip)], report=en_report, expect_indonesian=False)
    assert "Bahasa Indonesia" not in en_report[0]["warnings"]
    normalize_and_validate([dict(clip)], report=id_report)
    assert "tiktok_title_id terdeteksi bukan Bahasa Indonesia" in id_report[0]["warnings"]


def test_subtitle_languages_are_tried_in_configured_order(engine, monkeypatch, tmp_path):
    tried = []

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, *args, **kwargs):
            return {}

        def download(self, urls):
            if self.opts.get("skip_download"):
                tried.append(self.opts["subtitleslangs"][0])
            else:
                (tmp_path / "v.mp4").write_text("video")

    monkeypatch.setattr(engine, "YoutubeDL", FakeYDL)
    engine.download_video("https://youtube.com/watch?v=x", str(tmp_path / "v.mp4"),
                          use_dlp_subs=True, sub_langs=["ur", "en"])
    assert tried == ["ur", "en"]


def test_unknown_language_is_rejected():
    with pytest.raises(ValueError, match="Unsupported prompt language"):
        prompts.resolve_language(SimpleNamespace(prompt_language="fr"))
