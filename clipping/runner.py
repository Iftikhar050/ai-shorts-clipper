"""
clipping.runner — Pipeline Orchestrator

Maps to Cell 4 (Execute) of the notebook.
Orchestrates the full clip generation pipeline.
"""

import copy
import json
import os

from . import diarization as diarization_mod
from . import engine, metadata, scoring, selection, studio, hook_manager, voiceover


PROGRESS_PREFIX = "@@PROGRESS "


def _progress(stage: str, **data) -> None:
    """One machine-readable progress line for the web app (app/server.py)."""
    print(PROGRESS_PREFIX + json.dumps({"stage": stage, **data}, ensure_ascii=False), flush=True)


def _render_with_retry(klip, file_glitch_ts, data_segmen, cfg, video_encoder, diarization_data):
    """Render one clip, retrying failures; normalize loudness on success."""
    retries = max(0, int(getattr(cfg, "render_retries", 1)))
    result = None
    for attempt in range(retries + 1):
        if attempt:
            print(f"🔁 [Rank {klip['rank']}] Render gagal, mencoba lagi ({attempt}/{retries})...")
        result = studio.proses_klip(
            klip["rank"], klip, cfg.pilihan_rasio, file_glitch_ts, data_segmen, cfg,
            video_encoder, diarization_data=diarization_data,
        )
        if result and result.get("status") == "success":
            break
    if result:
        result["render_attempts"] = attempt + 1
        if result.get("status") == "success" and getattr(cfg, "loudnorm", False):
            from . import postprocess

            ok = postprocess.normalize_loudness(result.get("video_path", ""))
            result["loudness_normalized"] = ok
            if not ok:
                print(f"   ⚠️ [Rank {klip['rank']}] Normalisasi loudness gagal; klip asli dipertahankan.")
    return result


from .results import summarize_render  # noqa: E402  (re-exported for existing callers)


def _visual_review_enabled(cfg) -> bool:
    """--visual-rerank needs Gemini (vision) and a fresh analysis (not cached JSON)."""
    if not getattr(cfg, "visual_rerank", False):
        return False
    if getattr(cfg, "load_gemini_json", False):
        print("ℹ️ Visual review dilewati: memakai hasil AI cache (--load-gemini-json).")
        return False
    if getattr(cfg, "ai_provider", "gemini") != "gemini":
        print("ℹ️ Visual review hanya tersedia untuk Gemini; dilewati.")
        return False
    return True


def _collect_signals(cfg, video_info, source_platform):
    """Audience signals (YouTube) + FFmpeg events (any source), saved to outputs."""
    signals = _collect_audience_signals(cfg, video_info, source_platform)
    if getattr(cfg, "event_markers", False) and os.path.exists(cfg.file_video_asli):
        from . import events as events_mod

        scenes = getattr(cfg, "scene_detect", True)
        print(f"🎚️ Mendeteksi event audio{' & scene cut' if scenes else ''} dengan FFmpeg...")
        ev = events_mod.collect_events(cfg.file_video_asli, scenes=scenes)
        signals = signals or scoring.collect_signals(None)
        signals["events"] = ev
        print(f"   {len(ev['spikes'])} volume spike, {len(ev['cut_bursts'])} burst scene cut"
              + (f" | ⚠️ {'; '.join(ev['errors'])}" if ev["errors"] else ""))
    if signals is not None:
        scoring.save_signals(signals, os.path.join(cfg.outputs_dir, "audience_signals.json"))
    return signals


def _collect_audience_signals(cfg, video_info, source_platform):
    """Heatmap/chapters from the download's video info, plus optional comments."""
    if source_platform != "youtube" or not getattr(cfg, "audience_signals", True):
        return None
    comments = None
    if getattr(cfg, "use_comments", False):
        try:
            print(f"💬 Mengambil hingga {cfg.max_comments} komentar teratas untuk timestamp penonton...")
            comments = scoring.fetch_comments(
                cfg.url_youtube, cfg.max_comments, cookies=getattr(cfg, "yt_cookies", None)
            )
        except Exception as exc:  # comments are a bonus signal; never fail the run
            print(f"   ⚠️ Komentar tidak bisa diambil ({exc}). Lanjut tanpa komentar.")
    signals = scoring.collect_signals(video_info, comments)
    print(
        f"📊 Sinyal audiens: heatmap {'ADA' if signals['heatmap'] else 'tidak ada'}, "
        f"{len(signals['chapters'])} chapter, {len(signals['comment_marks'])} timestamp dari "
        f"{signals['comments_fetched']} komentar."
    )
    return signals


def run_pipeline(cfg, whisper_model_instance=None) -> list[dict]:
    """
    Run the full clipping pipeline:
      1. Download YouTube video
      2. Transcribe with Whisper
      3. Analyse with Gemini AI
      4. Normalize metadata
      5. Prepare glitch transition
      6. Render each clip
      7. Save render_manifest.json

    Parameters
    ----------
    cfg : SimpleNamespace
        Configuration object from ``config.build_config()``.
    whisper_model_instance : WhisperModel or None
        Optional pre-loaded WhisperModel to reuse across batch runs.

    Returns
    -------
    list[dict]
        Render manifest (one dict per clip).
    """

    from clipping.providers.validation import validate_analysis_config
    from clipping.providers.metadata import save_run_metadata

    validate_analysis_config(cfg, probe=getattr(cfg, "ai_provider", "gemini") == "vllm")

    # Step 1 — Download
    source_platform = getattr(cfg, "source_platform", "youtube")
    _progress("download")
    video_info = engine.download_video(
        cfg.url_youtube,
        cfg.file_video_asli,
        getattr(cfg, "use_dlp_subs", False),
        getattr(cfg, "download_source_height", "max"),
        source_platform=source_platform,
        yt_cookies=getattr(cfg, "yt_cookies", None),
        sub_langs=getattr(cfg, "sub_langs", None) or ("en", "id"),
    )

    # Step 2 — Transcribe
    _progress("transcribe")
    transkrip_lengkap = ""
    data_segmen = []

    import glob

    # Mencari file json3 apapun (karena bahasanya bisa .id.json3 atau .en.json3)
    json3_files = glob.glob(cfg.file_video_asli.replace(".mp4", ".*.json3"))
    file_json3 = json3_files[0] if json3_files else None

    # Only run YouTube JSON3 subtitle search for YouTube sources
    if source_platform == "youtube":
        if (
            getattr(cfg, "use_dlp_subs", False)
            and file_json3
            and os.path.exists(file_json3)
        ):
            transkrip_lengkap, data_segmen = engine.parse_youtube_json3_subs(
                file_json3, max_words_per_subtitle=cfg.max_kata_per_subtitle
            )
            if transkrip_lengkap and data_segmen:
                print(
                    f"✅ Berhasil memparsing subtitle dari YouTube ({os.path.basename(file_json3)}), melewati proses Whisper."
                )

    if not transkrip_lengkap or not data_segmen:
        transkrip_lengkap, data_segmen = engine.transcribe_video(
            cfg.file_video_asli,
            max_words_per_subtitle=cfg.max_kata_per_subtitle,
            model_size=cfg.whisper_model,
            device=cfg.whisper_device,
            compute_type=cfg.whisper_compute_type,
            whisper_model_instance=whisper_model_instance,
        )

    # Step 2b — Audience signals (YouTube heatmap / chapters / comments)
    _progress("signals")
    signals = _collect_signals(cfg, video_info, source_platform)
    transkrip_ai = scoring.annotate_transcript(transkrip_lengkap, signals)
    time_range = getattr(cfg, "time_range", None)
    if time_range:
        transkrip_ai = selection.filter_transcript(transkrip_ai, time_range)
        if not transkrip_ai.strip():
            raise ValueError(
                f"--time-range {time_range[0]:.0f}-{time_range[1]:.0f}s contains no speech in the transcript."
            )
        print(f"⏱️ Hanya menganalisis {time_range[0]:.0f}s-{time_range[1]:.0f}s dari video (--time-range).")
    visual_on = _visual_review_enabled(cfg)
    pool = scoring.candidate_pool(cfg.jumlah_clip, signals, force=visual_on)
    if pool != cfg.jumlah_clip:
        print(f"📊 Meminta {pool} kandidat klip ke AI, lalu memilih {cfg.jumlah_clip} terbaik dengan skor gabungan.")

    # Step 3 — Gemini AI analysis
    _progress("analyze", candidates=pool)
    gemini_output_path = os.path.join(cfg.outputs_dir, "gemini_response.json")

    if getattr(cfg, "load_gemini_json", False) and os.path.exists(gemini_output_path):
        print(f"\n🔄 [3/3] Memuat data AI ({cfg.ai_provider}) dari file lokal: {gemini_output_path}")
        with open(gemini_output_path, "r", encoding="utf-8") as f:
            hasil_json = json.load(f)
        save_run_metadata(cfg, cached=True)
    else:
        ai_cfg = copy.copy(cfg)
        ai_cfg.jumlah_clip = pool
        try:
            hasil_json = engine.analyze_with_ai(transkrip_ai, ai_cfg)
            cfg.ai_run_meta = getattr(ai_cfg, "ai_run_meta", None)
        except Exception:
            cfg.ai_run_meta = getattr(ai_cfg, "ai_run_meta", None)
            if getattr(cfg, "ai_run_meta", None):
                try:
                    save_run_metadata(cfg)
                except (OSError, TypeError, ValueError):
                    # A diagnostic write must not hide the inference failure.
                    print("[AI] Metadata kegagalan tidak dapat disimpan.")
            raise
        
        # Save raw gemini json for future loading/reproduction
        with open(gemini_output_path, "w", encoding="utf-8") as f:
            json.dump(hasil_json, f, indent=4, ensure_ascii=False)
        print(f"💾 Raw AI response tersimpan di: {gemini_output_path}")
        save_run_metadata(cfg)

    # Step 3b — Score breakdown + combined score with audience signals
    if isinstance(hasil_json, list):
        hasil_json = scoring.apply_score_breakdown(hasil_json)
        lo, hi = engine._duration_bounds(cfg)
        hasil_json, rejected = selection.enforce_duration(hasil_json, lo, hi, keep=cfg.jumlah_clip)
        for c in rejected:
            print(f"   ✂️ Kandidat {c.get('start_time')}-{c.get('end_time')}s dibuang: di luar panjang {lo}-{hi}s.")
    if isinstance(hasil_json, list) and visual_on and hasil_json:
        from . import visual

        _progress("visual", candidates=len(hasil_json))
        try:
            n = visual.score_candidates(
                hasil_json, cfg.file_video_asli, transkrip_lengkap, cfg, engine._generate_json_with_retry
            )
            print(f"   👁️ {n}/{len(hasil_json)} kandidat mendapat visual score.")
        except Exception as exc:  # visual review is a bonus; never fail the run
            print(f"   ⚠️ Visual review gagal ({exc}). Lanjut dengan ranking biasa.")
    if isinstance(hasil_json, list) and (
        scoring.has_signals(signals)
        or any("visual_score" in c for c in hasil_json)
        or len(hasil_json) > cfg.jumlah_clip
    ):
        hasil_json = scoring.rerank(
            hasil_json, signals, keep=cfg.jumlah_clip,
            weights=getattr(cfg, "score_weights", None),
        )
        for clip in hasil_json:
            comps = ", ".join(f"{k}={v:.2f}" for k, v in clip["score_components"].items())
            print(f"   #{clip['rank']} skor gabungan {clip['viral_score']} (AI {clip['ai_viral_score']}) | {comps}")

    # Step 4 — Metadata normalisation
    from clipping import prompts

    hasil_json = metadata.normalize_and_validate(
        hasil_json, expect_indonesian=prompts.resolve_language(cfg) == "id"
    )
    metadata.print_preview(hasil_json)

    metadata_path = os.path.join(cfg.outputs_dir, "metadata_preview.json")
    metadata.save_metadata_preview(hasil_json, path=metadata_path)

    # Step 5 — Diarization (split-screen / camera-switch)
    diarization_data = None
    if (
        (getattr(cfg, "use_split_screen", False) and cfg.split_trigger == "diarization")
        or getattr(cfg, "use_camera_switch", False)
    ) and studio._is_vertical_ratio(cfg.pilihan_rasio):
        try:
            mode_label = (
                "Split-Screen"
                if getattr(cfg, "use_split_screen", False)
                else "Camera-Switch"
            )
            print(f"\n🎙️ [{mode_label}] Menjalankan speaker diarization...")
            audio_path = cfg.file_video_asli.replace(".mp4", "_audio.wav")
            diarization_mod.extract_audio(cfg.file_video_asli, audio_path)
            num_speakers_arg = getattr(cfg, "diarization_num_speakers", 2)
            min_spk = None
            max_spk = None

            if str(num_speakers_arg).lower() == "auto":
                max_faces = studio.estimate_speaker_count_from_video(
                    cfg.file_video_asli, cfg
                )
                num_speakers_arg = "auto"
                min_spk = max(1, max_faces)
                max_spk = min_spk + 2
                print(f"   ℹ️ Instruksi Pyannote: {min_spk} hingga {max_spk} speaker.")

            diarization_data = diarization_mod.run_diarization(
                audio_path,
                hf_token=cfg.hf_token,
                num_speakers=num_speakers_arg,
                min_speakers=min_spk,
                max_speakers=max_spk,
            )
            # Clean up temp audio
            if os.path.exists(audio_path):
                os.remove(audio_path)
        except Exception as e:
            print(f"⚠️ Diarization gagal: {e}")
            print("   Fallback ke mode render biasa (tanpa split-screen).")
            diarization_data = None

    # Step 6 — Video encoder & glitch
    os.environ["OSC_VIDEO_SCALE_ALGO"] = str(
        getattr(cfg, "video_scale_algo", "lanczos")
    )
    
    # Get target dimensions for auto-bitrate calculation
    import cv2
    cap_e = cv2.VideoCapture(cfg.file_video_asli)
    src_h_e = int(cap_e.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap_e.release()
    
    target_w_e, target_h_e = studio._get_render_dims(cfg, cfg.pilihan_rasio, source_h=src_h_e)
    video_encoder = studio.detect_video_encoder(cfg, target_h=target_h_e)

    file_glitch_ts = None
    if cfg.use_hook_glitch:
        print("⚙️ Menyiapkan Video Glitch Transisi...")
        
        # Get source dimensions for proper glitch scaling
        import cv2
        cap_g = cv2.VideoCapture(cfg.file_video_asli)
        source_h_g = int(cap_g.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap_g.release()

        file_glitch_ts = studio.siapkan_glitch_video(
            cfg.pilihan_rasio, cfg, video_encoder, source_h=source_h_g
        )

    # Step 6 — Render each clip
    render_manifest: list[dict] = []

    custom_hook_path = None
    if getattr(cfg, "hook_source", None):
        print("\n🎣 Mengunduh sumber klip Hook kustom...")
        custom_hook_path = hook_manager.download_custom_hook(cfg)

    # Step 5.5 — Generate Voice-Over (if enabled)
    if getattr(cfg, "voiceover", False):
        print(f"\n🎙️ Meng-generate Voice-Over untuk {len(hasil_json)} klip...")
        for klip in hasil_json:
            try:
                # 1. Generate commentary script from snippet
                start = float(klip["start_time"])
                end = float(klip["end_time"])
                # Extract transcript snippet for this time range
                snippet_lines = []
                for seg in data_segmen:
                    if float(seg["end"]) > start and float(seg["start"]) < end:
                        # Support both Whisper format (has 'text') and YouTube JSON3 (only 'words')
                        seg_text = seg.get("text") or " ".join(w["word"] for w in seg.get("words", []))
                        if seg_text:
                            snippet_lines.append(seg_text)
                snippet_text = " ".join(snippet_lines)

                script = voiceover.generate_commentary_script(
                    snippet_text,
                    cfg,
                    style=cfg.voiceover_style,
                    language=cfg.voiceover_lang,
                    length=cfg.voiceover_length,
                )

                if script:
                    # 2. Synthesize TTS
                    audio_path, vo_segments = voiceover.synthesize_voice(
                        script,
                        cfg.voiceover_voice,
                        cfg.outputs_dir,
                        str(klip["rank"])
                    )
                    
                    if os.path.exists(audio_path):
                        klip["voiceover"] = {
                            "script": script,
                            "audio_path": audio_path,
                            "segments": vo_segments,
                            "voice": cfg.voiceover_voice
                        }

            except Exception as e:
                print(f"   ⚠️ Gagal generate voice-over untuk Rank {klip['rank']}: {e}")

    ordered = sorted(hasil_json, key=lambda x: x["rank"])
    for index, klip in enumerate(ordered, 1):
        _progress("render", clip=index, total=len(ordered), rank=klip["rank"])

        if custom_hook_path:
            klip["custom_hook_info"] = {"file_path": custom_hook_path}

        hasil_render = _render_with_retry(
            klip, file_glitch_ts, data_segmen, cfg, video_encoder, diarization_data
        )
        if hasil_render:
            render_manifest.append(hasil_render)

    # Step 7 — Inject source metadata for attribution & safety tracking
    for row in render_manifest:
        # Attach source URL so metadata.py can auto-add source credit
        if not row.get("source_url"):
            row["source_url"] = getattr(cfg, "url_youtube", None)

    # Step 8 — Save manifest
    manifest_path = os.path.join(cfg.outputs_dir, "render_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(render_manifest, f, ensure_ascii=False, indent=2)

    print(
        f"\n💾 Render manifest disimpan ke {manifest_path} ({len(render_manifest)} item)"
    )
    ok, failed, _ = summarize_render(render_manifest)
    _progress("done", rendered=ok, failed=failed)


    return render_manifest

