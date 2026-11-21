"""Configurable target accounts: loading, validation, prompt and schema wiring."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from clipping.accounts import AccountsError, DEFAULT_ACCOUNTS, load_accounts, resolve_accounts
from clipping.providers.schema import build_clips_schema

ROOT = Path(__file__).resolve().parents[1]

SAMPLE = {
    "accounts": {
        "Sports": {"handle": "@me.sports", "angle_desc": "Match highlights.", "bio": "Daily highlights"},
        "Science": {"angle_desc": "Brain and health explainers."},
    },
    "rules": ["Athlete health stories go to Science.", "  "],
}


def _write(tmp_path, data, name="accounts.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")
    return path


@pytest.fixture
def engine(monkeypatch):
    for name, symbol in (("yt_dlp", "YoutubeDL"), ("faster_whisper", "WhisperModel")):
        stub = ModuleType(name)
        setattr(stub, symbol, object)
        monkeypatch.setitem(sys.modules, name, stub)
    spec = importlib.util.spec_from_file_location("_accounts_engine", ROOT / "clipping" / "engine.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cfg(accounts, rules=(), classify=True):
    return SimpleNamespace(target_accounts=accounts, account_rules=list(rules), account_classify=classify)


def test_load_accounts_normalizes_defaults_and_rules(tmp_path):
    accounts, rules = load_accounts(_write(tmp_path, SAMPLE))
    assert list(accounts) == ["Sports", "Science"]
    assert accounts["Sports"] == {"akun_tujuan": "@me.sports", "angle_desc": "Match highlights.", "bio": "Daily highlights"}
    assert accounts["Science"] == {"akun_tujuan": "Science", "angle_desc": "Brain and health explainers.", "bio": ""}
    assert rules == ["Athlete health stories go to Science."]


@pytest.mark.parametrize("data, message", [
    ("{not json", "not valid JSON"),
    ({"accounts": {}}, "at least one account"),
    ({"Sports": {"angle_desc": "x"}}, '"accounts" object'),
    ({"accounts": {"Sports": {"handle": "@x"}}}, "angle_desc"),
    ({"accounts": {"Sports": {"angle_desc": "x", "handle": " "}}}, "empty \"handle\""),
    ({"accounts": {"Sports": {"angle_desc": "x"}}, "rules": "one rule"}, "rules"),
])
def test_invalid_accounts_file_is_rejected(tmp_path, data, message):
    with pytest.raises(AccountsError, match=message):
        load_accounts(_write(tmp_path, data))


def test_resolve_prefers_explicit_then_local_file_then_builtin(tmp_path):
    other = _write(tmp_path, {"accounts": {"Other": {"angle_desc": "x"}}}, "other.json")
    assert resolve_accounts(None, str(tmp_path))[0] == DEFAULT_ACCOUNTS
    _write(tmp_path, SAMPLE)
    assert list(resolve_accounts(None, str(tmp_path))[0]) == ["Sports", "Science"]
    assert list(resolve_accounts(str(other), str(tmp_path))[0]) == ["Other"]
    accounts, rules, source = resolve_accounts(str(other), str(tmp_path), classify=False)
    assert (accounts, rules, source) == (DEFAULT_ACCOUNTS, [], "off")
    with pytest.raises(AccountsError, match="not found"):
        resolve_accounts(str(tmp_path / "missing.json"), str(tmp_path))


def test_prompt_uses_configured_accounts_and_rules(engine, tmp_path):
    accounts, rules = load_accounts(_write(tmp_path, SAMPLE))
    prompt = engine.get_analysis_prompt("[0 - 1] hi", 2, 3, cfg=_cfg(accounts, rules))
    assert "- Sports: Match highlights." in prompt
    assert 'akun_tujuan: "@me.sports"' in prompt
    assert "1. Athlete health stories go to Science." in prompt
    assert '"tipe_akun": "Sports"' in prompt and '"akun_tujuan": "@me.sports"' in prompt
    assert '"tipe_akun": "Science"' in prompt  # alternatif_akun example
    assert "Mereska" not in prompt and "Muslim" not in prompt


@pytest.mark.parametrize("cfg_factory", [
    lambda accounts: None,
    lambda accounts: _cfg(DEFAULT_ACCOUNTS),
    lambda accounts: _cfg(accounts, classify=False),
])
def test_general_or_disabled_classification_uses_short_section(engine, tmp_path, cfg_factory):
    accounts, _ = load_accounts(_write(tmp_path, SAMPLE))
    prompt = engine.get_analysis_prompt("[0 - 1] hi", 1, 3, cfg=cfg_factory(accounts))
    assert 'tipe_akun "General"' in prompt
    assert "ATURAN KHUSUS KLASIFIKASI" not in prompt
    assert "Mereska" not in prompt


def test_schema_enum_follows_configured_accounts(engine, tmp_path):
    accounts, _ = load_accounts(_write(tmp_path, SAMPLE))
    schema = build_clips_schema(engine._accounts_for(_cfg(accounts)))
    props = schema["items"]["properties"]["klasifikasi_akun"]["properties"]
    assert props["tipe_akun"]["enum"] == ["Sports", "Science"]
    assert build_clips_schema(engine._accounts_for(None))["items"]["properties"]["klasifikasi_akun"][
        "properties"]["tipe_akun"]["enum"] == ["General"]


def test_cli_flags_load_accounts(tmp_path, monkeypatch):
    from clipping.config import build_config

    monkeypatch.chdir(tmp_path)
    path = _write(tmp_path, SAMPLE, "mine.json")
    cfg = build_config(["--url", "https://youtube.com/watch?v=x", "--accounts-file", str(path)])
    assert list(cfg.target_accounts) == ["Sports", "Science"] and cfg.account_classify
    cfg = build_config(["--url", "https://youtube.com/watch?v=x", "--no-account-classify"])
    assert cfg.target_accounts == DEFAULT_ACCOUNTS and not cfg.account_classify
    with pytest.raises(SystemExit):
        build_config(["--url", "https://youtube.com/watch?v=x", "--accounts-file", str(tmp_path / "nope.json")])
