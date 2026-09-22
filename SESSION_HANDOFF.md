# VoiceBench session handoff

Updated: 2026-09-23 (Asia/Seoul)

This file is the starting point for the next Codex session. Read it before making changes. Do not use Pinokio or Gepeto in this project.

## Current goal

Run the best practical local voice-cloning TTS service on the owner's PC, using the owner's fixed reference voice. The web UI and LAN MCP/API should produce the same speech when the script, engine, seed, and quality mode are identical.

## Current operating state

- Workspace: `C:\Users\alseo\Documents\Voice`
- LAN web URL: `http://192.168.219.135:8765`
- Local URL: `http://localhost:8765`
- The server is currently offline: nothing is listening on port 8765 and no VoiceBench/Qwen/Whisper worker is running.
- There are no queued or running TTS requests. Do not start the server unless the user asks or the next task requires it.
- The old ngrok public URL is intentionally offline. Current operation is LAN-only.
- Web authentication and external Bearer authentication are separate. Never commit their secret values.
- The external API key can be checked locally with `show-api-key.cmd`.

Start the LAN server:

```powershell
$env:VOICEBENCH_ACCESS_KEY = '<web password>'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\host.ps1 -BindAddress 0.0.0.0 -NoTunnel -Background
```

Stop the server:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\stop-hosting.ps1
```

If the Qwen worker remains on port 8790 after stopping, send `POST {}` to `http://127.0.0.1:8790/shutdown`.

## Selected production settings

- Reference WAV: `data\prepared\ref_voice\selected_reference.wav`
- Reference transcript: `data\prepared\ref_voice\selected_reference.txt`
- Selected reference: B, about 9.95 seconds
- Default engine slug: `qwen3-tts-17b-pascal-expressive`
- Display preset: `Qwen3-TTS 1.7B · 표현형 T1.0/P0.95`
- Default/fixed Seed: `13`
- Default mode: `long_form_hq`
- Hardware: GTX 1070 Ti 8 GB; the 1.7B worker runs FP16/eager and normally uses about 5.8-6.5 GB VRAM.

Registered Qwen engines:

- `qwen3-tts-17b-pascal-expressive`: T1.0/P0.95, current default
- `qwen3-tts-17b-pascal`: official-like T0.9/P1.0
- `qwen3-tts-17b-pascal-stable`: T0.75/P0.90
- `qwen3-tts-06b-pascal`: older 0.6B comparison engine

## Web and MCP/API parity

The web Studio default and external MCP/API both call `Service.create_studio_request` and therefore use the same:

- reference audio and transcript;
- Qwen 1.7B expressive engine;
- Seed 13 as the primary candidate (server-controlled QA fallbacks only after a detected failure);
- semantic speech planning and approximately 180-character sentence-first segments;
- long-form HQ chapter grouping and final WAV concatenation.

For a script around 10 minutes, both paths should sound the same when the input text is exactly the same, including punctuation. The request source (`studio` or `external_api`) is metadata only. CUDA does not provide a mathematical guarantee that independently generated WAV files will always be byte-for-byte identical, although the RNG is reseeded for each segment and listening results should be equivalent on this PC.

The web UI still allows manual engine and seed changes for comparison. MCP/API behavior is intentionally fixed:

- MCP tool schemas do not expose a `seed` input.
- The external `POST /v1/tts` accepts only the script for normal use.
- If an old client still sends any `seed` value, including an invalid type, the server ignores it and uses 13.
- The external client helper also no longer exposes `--seed`.
- Restart Claude Desktop after MCP schema changes so it reloads the tools.
- MCP requests with 8 or more generation segments return a persistent request ID immediately. Claude must use `voicebench_tts_status` and must not resubmit the script.
- A synchronous wait timeout returns the existing request ID and current status instead of raising a terminal timeout error.
- Identical active requests and identical successful requests from the last 24 hours are reused automatically.
- External status includes `download_count` and `last_downloaded_at`; a completed full-file GET updates this delivery record.

MCP bridge: `tools\voicebench_mcp.py`

MCP setup guide: `docs\claude-lan-mcp.md`

## Long-form HQ behavior

- There is no application-level character limit; the HTTP body limit is 2 MB.
- Scripts target about 180 characters per generation segment, keep complete sentences and paragraphs whenever possible, and use a 240-character hard safety ceiling only for unusually long single sentences.
- Segment WAVs are trimmed by measured PCM silence while retaining 40ms of safe edge padding. The join targets 170ms after commas/colons, 280ms after periods, 320ms after questions/exclamations, and 220ms otherwise.
- Fade/crossfade and loudness matching are intentionally not implemented yet; the user chose to evaluate those later.
- Segments are grouped into chapters targeting about 1,800 characters, with a 900-character minimum for a trailing chapter.
- Speech planning classifies opening, question, transition, conclusion, exclamation, and statement units without rewriting the user's words. It keeps questions with their setup when practical, starts a new breath before substantial transition phrases, preserves paragraph metadata, and prefers paragraph boundaries for chapter cuts.
- Planned total boundary pauses are 420ms for ordinary paragraph ends, 500ms after question paragraphs, 450ms after exclamation paragraphs, and at least 460ms before transition/conclusion units. Existing generated edge silence is measured before adding only the missing amount.
- Every Qwen long-form HQ segment is checked locally by a persistent CPU-only Whisper Base worker. The normal first candidate remains Seed 13. Only high-confidence omissions, repetitions, invalid waveforms, or implausible durations trigger server-controlled fallback candidates (Seed 10, then 42), and the lowest-risk candidate is promoted.
- Studio/API responses expose aggregate `quality_control` counts. Per-run metrics and manifests retain every attempt's transcript, score, reason, and the actually selected Seed. External callers still cannot choose a Seed.
- Each completed chapter is written to `data\artifacts\studio\request-{id}-chapter-{NN}.wav`.
- The final combined file is `data\artifacts\studio\request-{id}.wav`.
- The UI and external status response expose chapter progress and completed chapter audio.
- Jobs run serially on the single GPU. New web and MCP requests share the same queue.
- Cancellation and interrupted-job recovery have been implemented.

External endpoints:

- `POST /v1/tts`
- `GET /v1/tts/{id}`
- `GET /v1/tts/{id}/audio`
- `GET /v1/tts/{id}/chapters/{chapter}/audio`
- `GET /v1/health`

External limits:

- Up to 4 queued/running external requests
- Up to 10 new external requests per minute

## Important implementation files

- `voicebench/service.py`: defaults, segmentation, chapters, Studio/external request model
- `voicebench/runner.py`: serial queue execution, chapter WAVs, final concatenation, cancellation guard
- `voicebench/api.py`: web and external HTTP routes, auth, fixed external seed
- `voicebench/engines.py`: engine launch and Qwen worker model switching
- `adapters/qwen3_tts_worker.py`: persistent Qwen generation worker and RNG seeding
- `tools/register_qwen3_tts.py`: engine registration and sampling presets
- `tools/voicebench_mcp.py`: Claude Desktop stdio MCP bridge
- `voicebench/static/app.js`: Studio request payload, queue and chapter UI
- `tests/test_harness.py`: current integration/regression coverage

## Verification

The latest full test run passed all 18 tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Also run before handing off changes:

```powershell
git diff --check
```

Normal LF-to-CRLF warnings may appear on Windows and are not test failures.

## Git state

Last committed revisions:

- `6b2df00 feat: improve LAN TTS long-form quality`
- `23f9cc7 docs: add AI usage guide for external TTS API`
- `2582685 feat: build local voice cloning TTS service`

The 1.7B engines, Seed 13 defaults, LAN/MCP integration, queue/cancellation fixes, long-form HQ chapters, semantic planning, automatic quality control, UI changes, tests, and documentation are included in `6b2df00`. Preserve any newer working-tree changes and inspect `git status` before editing overlapping files.

## Known cleanup item

`AI_USAGE.md` and `docs/external-api.md` still contain examples using the old ngrok public hostname. They are stale for the current LAN-only setup. Do not assume that URL is live. Update the documents if the next task concerns deployment or external connection instructions.

## 2026-08-17 long MCP incident

- External request `#65` took 1 hour 5 seconds to generate a 13 minute 21 second WAV and succeeded.
- The old MCP wait path timed out/retried near the one-hour boundary and created duplicate request `#66` with the exact same script.
- `#66` was cancelled after four segments; `#65` remains the valid completed result.
- The server now deduplicates identical active and recent successful requests, long MCP calls return an ID immediately, wait timeout preserves the ID, and completed full-file downloads are audited.
- The updated `tools\voicebench_mcp.py` must be copied to the MAX PC and Claude Desktop restarted to receive the new async tool behavior. Server-side deduplication protects old clients immediately even before that copy.

## 2026-08-22 natural chunk stitching

- The old 5,957-character request `#65` used 75 segments; only 36% ended at a natural punctuation boundary.
- The new sentence-first splitter produces 37 segments for the same script, averages 160 characters, and all 37 end at natural punctuation boundaries.
- The old join always inserted 220ms even though generated files already contained their own edge silence; one measured segment had 440ms of leading silence.
- New joins analyze and trim real edge silence, then enforce punctuation-aware total pauses without fades or volume normalization.
- A non-regenerated comparison using the old `#65` segment WAVs is available at `data\artifacts\studio\request-65-restitch-v2.wav`. New requests also benefit from the longer sentence-first generation segments.

## 2026-08-29 automatic quality control

- Added `adapters\whisper_quality_worker.py` and `voicebench\quality.py` using the already cached OpenAI Whisper Base model through the existing Qwen runtime. It stays resident on CPU and does not consume Qwen's GPU memory.
- Automatic retries apply only to Qwen Studio/MCP/API requests in long-form HQ mode. Benchmarks retain their explicitly requested Seed and are not altered.
- Primary generation is always the requested/default Seed (external default 13). A failed primary may try server-controlled Seeds 10 and 42; the best scored candidate is copied to the canonical job output.
- If a fallback wins, the stored per-job WAV filename uses the actually selected Seed (for example `seed-10.wav`) rather than the failed base Seed.
- Request `#70` was the production smoke test: one Seed 13 attempt, Whisper character error rate 0.0, no retry, 8.96-second output, succeeded.
- The regression suite includes forced retry and selected-Seed recording.

## 2026-08-29 semantic speech planning

- Added `SpeechSegment` and `plan_script()` in `voicebench\service.py`. Each new Studio/MCP/API segment stores paragraph index, sentence count, semantic role, paragraph-end state, and planned pause in `studio_segments`.
- `voicebench\audio.py` accepts explicit boundary pause plans; `voicebench\runner.py` applies them both within chapters and between chapters.
- Studio and external status responses expose a `speech_plan` summary, and the web history shows paragraph/breath and expressive-unit counts.
- External fingerprint version is now 4, so pre-planner completed requests are not reused as equivalent outputs.
- Production sample request `#71`: 361 characters, 4 paragraphs, 4 semantic units (opening/question/transition/conclusion), 45.14 seconds, all four Whisper checks passed on Seed 13 with no retry or warning. Downloaded sample: `data\artifacts\studio\semantic-speech-plan-sample.wav`.
- The full regression suite now has 18 passing tests.

## Latest completed production requests

- External request `#72`: succeeded, 42 semantic segments, 751.43 seconds of audio, downloaded once. All 42 Whisper checks passed on Seed 13 with no retry or warning; average CER 0.0415 and maximum CER 0.1026.
- External request `#73`: succeeded, 45 semantic segments, 816.02 seconds of audio, downloaded once. All 45 Whisper checks passed on Seed 13 with no retry or warning; average CER 0.0725 and maximum CER 0.2614.
- External request `#75`: 267-character emotional-continuity sample, 2 segments, 35.29 seconds. Both segments passed on Seed 13 without retry. The measured join had the planned 360ms pause and no waveform discontinuity; the second segment began about 1.35 semitones and 1.31dB above the end of the first, confirming that mild prosody reset remains possible.

## Windows clone installation

- `docs\windows-install.md` documents a clean Windows clone, model setup, private reference transfer, local/LAN launch, MCP/API configuration, updates, and troubleshooting.
- `setup-windows.cmd` orchestrates the application environment, isolated Qwen engine environment, optional production model downloads, optional local reference installation, and the full regression suite.
- Personal reference audio, transcripts, generated audio, API keys, passwords, model caches, and virtual environments remain excluded from Git.

## Recommended next development priority

- The next previously agreed quality priority is cross-segment continuity: supply controlled previous-sentence context or use an overlap/alignment strategy so independently generated chunks do not reset cadence at every boundary.
- Implement it as an opt-in comparison first. It is higher risk than the completed speech planner because naive text overlap can duplicate words, and audio crossfade alone cannot repair mismatched prosody.
- Emotion-specific reference recordings and Korean pronunciation normalization remain later priorities. Fade/crossfade and loudness matching are still intentionally deferred unless the user chooses to revisit them.

## New-session startup

- No special startup prompt is required. Root `AGENTS.md` instructs every new session to read this file completely, preserve the dirty worktree, and inspect Git/server/queue state before acting.
- Pinokio and Gepeto remain prohibited for this project.
