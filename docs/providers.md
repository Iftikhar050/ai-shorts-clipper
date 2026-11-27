# AI providers

Clip analysis supports three providers via `--ai-provider`:

| Provider | Use | Needs |
|---|---|---|
| `gemini` (default) | Google Gemini, cloud | `GOOGLE_API_KEY` |
| `nvidia` | NVIDIA NIM, cloud (falls back to Gemini on failure) | `NVIDIA_API_KEY`, `--nvidia-model` |
| `vllm` | Your own self-hosted model server (OpenAI-compatible) | A running vLLM server |

Visual review (`--visual-rerank`) and the web app's defaults use Gemini.

## Self-hosted vLLM

vLLM is a separate model server; this project only includes its HTTP client.
Start the server yourself (it needs a capable GPU), then:

```powershell
.\.venv\Scripts\python.exe main.py --url "VIDEO_URL" --ai-provider vllm --clips 1 `
  --whisper-device cpu --whisper-compute-type int8
```

| Environment variable | Default | Purpose |
|---|---|---|
| `VLLM_BASE_URL` | `http://127.0.0.1:8001` | Server root; an optional trailing `/v1` is accepted |
| `VLLM_MODEL` | `google/gemma-4-E2B-it-qat-w4a16-ct` | Exact served model ID or alias from `/v1/models` |
| `VLLM_API_KEY` | empty | Optional server authentication |
| `VLLM_MAX_TOKENS` | `4096` | Maximum generated tokens per analysis request |
| `VLLM_CONTEXT_LIMIT` | unset | Optional conservative context ceiling, in tokens |
| `VLLM_TIMEOUT` | `900` | Overall analysis/retry time budget in seconds |

CLI overrides: `--vllm-model`, `--local-base-url`, `--local-max-tokens`,
`--local-context-limit`, `--local-timeout`. The context flag does **not**
increase the server's `--max-model-len`.

## Request and failure behaviour

- **Gemini:** Developer API `v1beta`, JSON schema output, retries; after two
  "high demand"/quota errors in a row it switches to `--gemini-fallback-model`.
- **NVIDIA:** NIM request fields and SDK retry defaults, then Gemini fallback.
- **vLLM:** never falls back to a cloud provider. The client checks
  `/v1/models`, then `/tokenize`; prompt tokens plus the output budget must fit
  the server's context, otherwise it fails clearly instead of truncating the
  transcript. Output uses `response_format: json_schema`. Timestamps are
  validated (clip, hook, keep-segments, B-roll); invalid ones are rejected,
  not silently changed. At most three attempts; authentication, missing model,
  connection refusal, context overflow and `finish_reason=length` fail
  without retrying.
- vLLM clients don't follow redirects or use proxy settings, and reject
  endpoints with embedded credentials, query strings or link-local targets.
  Only use endpoints you trust.

The raw result is saved as `gemini_response.json` (name kept for
compatibility) and `ai_run_meta.json` records the model, attempts, token usage
and duration (no transcript text). A content hash ties the metadata to the
saved response, so a failed re-run can't claim an older result.

## When a transcript is too long for a local model

An 8,192-token server with a 2,048-token output budget has room for about
6,144 input tokens, including the instructions. For a long video either use a
shorter excerpt (`--time-range`), a model/server with a larger context, or a
cloud provider (which sends the transcript to that provider).

## Tests

The provider tests use fakes and mock HTTP; no model server is needed:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_provider_cloud_contracts.py tests/test_provider_metadata.py `
  tests/test_gemini_routing.py tests/test_vllm_provider.py tests/test_vllm_validation.py tests/test_vllm_cli.py
```

A live test against a running vLLM server is opt-in:
`RUN_LOCAL_LLM_TESTS=1 python -m pytest -m local_llm tests/test_vllm_live.py`.
