"""Run/cache provenance contracts for saved AI responses."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipping.providers import metadata


def _cfg(tmp_path, **overrides):
    values = dict(outputs_dir=str(tmp_path), ai_provider="vllm")
    values.update(overrides)
    return SimpleNamespace(**values)


def _read(tmp_path):
    return json.loads((tmp_path / "ai_run_meta.json").read_text(encoding="utf-8"))


def _completed(model="local-model"):
    return dict(actual_provider="vllm", model=model, status="completed", attempts=1)


def test_completed_run_hashes_saved_response_and_repeated_cache_preserves_origin(tmp_path):
    response = b'[{"rank": 1}]'
    (tmp_path / "gemini_response.json").write_bytes(response)
    cfg = _cfg(tmp_path, ai_run_meta=_completed())
    metadata.save_run_metadata(cfg)
    digest = hashlib.sha256(response).hexdigest()
    assert _read(tmp_path)["response_sha256"] == digest

    cfg.ai_provider = "gemini"
    for _ in range(2):
        metadata.save_run_metadata(cfg, cached=True)
        saved = _read(tmp_path)
        assert saved["actual_provider"] == "vllm"
        assert saved["model"] == "local-model"
        assert saved["requested_provider"] == "gemini"
        assert saved["response_sha256"] == digest
        assert saved["cache_hit"] is True
        assert saved["attempts"] == saved["duration_seconds"] == 0


def test_modified_cache_loses_old_provider_attribution(tmp_path):
    response = tmp_path / "gemini_response.json"
    response.write_text('[{"rank": 1}]', encoding="utf-8")
    cfg = _cfg(tmp_path, ai_run_meta=_completed())
    metadata.save_run_metadata(cfg)
    response.write_text('[{"rank": 2}]', encoding="utf-8")

    metadata.save_run_metadata(cfg, cached=True)
    saved = _read(tmp_path)
    assert saved["actual_provider"] is None
    assert saved["model"] is None
    assert saved["response_sha256"] == hashlib.sha256(response.read_bytes()).hexdigest()


def test_failed_rerun_cannot_claim_previous_response(tmp_path):
    response = tmp_path / "gemini_response.json"
    response.write_text('[{"rank": 1}]', encoding="utf-8")
    cfg = _cfg(tmp_path, ai_run_meta=_completed("previous-model"))
    metadata.save_run_metadata(cfg)
    cfg.ai_run_meta = dict(
        actual_provider="vllm", model="failed-model", status="failed", attempts=3,
        error_type="InvalidOutputError", response_sha256=_read(tmp_path)["response_sha256"],
    )
    metadata.save_run_metadata(cfg)
    failed = _read(tmp_path)
    assert failed["status"] == "failed"
    assert failed["model"] == "failed-model"
    assert "response_sha256" not in failed
    assert response.read_text(encoding="utf-8") == '[{"rank": 1}]'

    metadata.save_run_metadata(cfg, cached=True)
    assert _read(tmp_path)["actual_provider"] is None
    assert _read(tmp_path)["model"] is None


@pytest.mark.parametrize("previous", [None, "broken JSON", "[]", '{"actual_provider":"vllm","model":"legacy"}'])
def test_cache_without_verifiable_metadata_has_unknown_origin(tmp_path, previous):
    (tmp_path / "gemini_response.json").write_text("[]", encoding="utf-8")
    if previous is not None:
        (tmp_path / "ai_run_meta.json").write_text(previous, encoding="utf-8")
    metadata.save_run_metadata(_cfg(tmp_path), cached=True)
    saved = _read(tmp_path)
    assert saved["actual_provider"] is None
    assert saved["model"] is None
    assert saved["cache_hit"] is True


def test_failed_metadata_is_rejected_even_if_its_hash_matches_cache(tmp_path):
    response = b"[]"
    (tmp_path / "gemini_response.json").write_bytes(response)
    previous = dict(_completed(), status="failed", response_sha256=hashlib.sha256(response).hexdigest())
    (tmp_path / "ai_run_meta.json").write_text(json.dumps(previous), encoding="utf-8")
    metadata.save_run_metadata(_cfg(tmp_path), cached=True)
    assert _read(tmp_path)["actual_provider"] is None


def test_legacy_provider_success_does_not_invent_model_or_fallback_origin(tmp_path):
    (tmp_path / "gemini_response.json").write_text("[]", encoding="utf-8")
    metadata.save_run_metadata(_cfg(tmp_path, ai_provider="nvidia"))
    saved = _read(tmp_path)
    assert saved["requested_provider"] == "nvidia"
    assert saved["actual_provider"] is None
    assert saved["model"] is None
    assert saved["instrumented"] is False
    assert saved["status"] == "completed"


def test_completed_metadata_without_cache_has_no_hash(tmp_path):
    metadata.save_run_metadata(_cfg(tmp_path, ai_run_meta=_completed()))
    assert "response_sha256" not in _read(tmp_path)
