# VoiceBench session handoff

Updated: 2026-09-26 (Asia/Seoul)

This file is the starting point for the next Codex session. Read it before making changes. Do not use Pinokio or Gepeto in this project.

## Current goal

Run the best practical local voice-cloning TTS service on the owner's PC, using the owner's fixed reference voice. The web UI and LAN MCP/API should produce the same speech when the script, engine, seed, and quality mode are identical.

Latest owner feedback: after selecting story A, the owner also said “설명형 대화형 둘다 a가 더 좋아.” All three tested scripts now favor A = `full_text` (`non_streaming_mode=True`) in the **input-mode A/B** experiment, `data/comparisons/shorts-input-mode-20260925/listen.html`. These labels are unrelated to sampling A–D or continuity C. Explanation/conversation were delivered as lossless inline chat players, with source WAV hashes and decoded PCM checked. The September 25 missing-feedback audit below is historical. Keep the already implemented single-generation full-text policy; no further code change or server restart was needed for this feedback.

## Current operating state

- Workspace: `C:\Users\alseo\Documents\Voice`
- Active local URL: `http://127.0.0.1:8765` (also `http://localhost:8765`).
- Previous LAN URL: `http://192.168.219.135:8765`; the current run binds only to loopback, so LAN access is not enabled.
- The owner requested server startup on 2026-09-26. VoiceBench is running from this workspace on port 8765, PID 30448 at launch, with its normal background job runner. Confirm live state before restarting or stopping it.
- No saved web password was available in process/user/machine environment or the runtime files. Started the normal local web mode, retained the existing external Bearer key in process memory, and opened the local URL in Codex. No credentials were printed or newly stored. For LAN hosting, use the authenticated `host.ps1` flow below with the owner's web password.
- Story request #76, submitted through `POST /api/studio/synthesize`, succeeded: 24.63s, one segment, `single_segment_full_text_v1`, `non_streaming_mode=True`, Seed 13 first attempt passed. The audio GET returned 200 and matched the stored WAV hash. Final verification results are in `.runtime/server-launch-check.json`; output is `data/artifacts/studio/request-76.wav`. Queue was empty after completion. Server and its normal TTS workers are intentionally left running; inspect live queue state before any process changes.
- The old ngrok public URL is intentionally offline. Current operation is local-only; the server was previously used on LAN.
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
- Default expressive HQ requests with exactly one actual generation segment now use `non_streaming_mode=True`, selected from story A. Multi-segment requests retain the original input mode even when only one queued job remains. Explicit engine input-mode settings, comparison engines, standard mode and benchmarks are preserved.
- Hardware: GTX 1070 Ti 8 GB; the 1.7B worker runs FP16/eager and normally uses about 5.8-6.5 GB VRAM.

Registered Qwen engines:

- `qwen3-tts-17b-pascal-expressive`: T1.0/P0.95, current default
- `qwen3-tts-17b-pascal`: official-like T0.9/P1.0
- `qwen3-tts-17b-pascal-stable`: T0.75/P0.90
- `qwen3-tts-06b-pascal`: older 0.6B comparison engine
- `qwen3-tts-17b-pascal-expressive-continuity`: optional anchored-context comparison, HQ only; never the default

## Web and MCP/API parity

The web Studio default and external MCP/API both call `Service.create_studio_request` and therefore use the same:

- reference audio and transcript;
- Qwen 1.7B expressive engine;
- Seed 13 as the primary candidate (server-controlled QA fallbacks only after a detected failure);
- semantic speech planning and approximately 180-character sentence-first segments;
- the selected full-text input policy for exactly one generation segment in default expressive HQ;
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

The latest full test run passed all 53 tests:

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

- Latest user clarification: prioritize roughly 30-second shorts. One-generation shorts have no join to improve. The old C experiment used a 140-character target; the same 216-character script is ONE segment under the actual default 180-character planner. Do not force extra splits to imitate C or treat C's two-segment preference as proof of a single-generation improvement.
- New single-generation comparison completed: `data/comparisons/shorts-20260925/listen.html`, 3 scripts × 4 sampling conditions. All 12 passed waveform/ASR checks. A–D are independently shuffled per script and unrelated to previous C. User listening preference was requested but has not arrived. Do not promote a condition by CER alone. Details: `docs/shorts-quality-20260925.md`.
- User listening feedback on `continuity-20260924-verified/listen.html`: **C sounded best**. Verified labels are A=baseline, B=longer_turns, C=anchored. Prioritize anchored continuity for further comparisons. This is a preference on one sample, not an instruction to change the production default.
- Evaluate the opt-in anchored Studio preset and `tools/compare_continuity.py` on varied scripts and a full long-form request before changing production generation defaults. One short sample does not establish perceptual improvement.
- The experiment keeps the original speaker embedding and uses a verified complete previous segment as acoustic context. It does not prepend duplicate text or guess word-aligned waveform cuts. It resets at paragraph/transition boundaries and after one hop.
- Emotion-specific reference recordings and Korean pronunciation normalization remain later priorities. Fade/crossfade and loudness matching are still intentionally deferred unless the user chooses to revisit them.

## 2026-09-24 quality review and corrections

- Ran `git pull --ff-only`; `main` was already current at `7a1c64f`. Initial worktree was clean. Current implementation changes are intentionally uncommitted.
- Preserved selected reference B, expressive engine, Seed 13 and HQ defaults. No production database requests were created or changed during the comparison.
- Fixed speech planner false transitions (`자연스럽게`), comma-followed transition words, long-question ending roles, numeric comma/colon cuts, and tail merging that erased deliberate question/transition boundaries. Existing #72/#73 scripts still produce 42/45 segments.
- QA now prefers a passing candidate over a failing lower-CER candidate, discounts only repetitions present in the script, rejects empty WAVs, and recognizes the full audio beyond Whisper's former 30-second truncation. External request fingerprint is now 5.
- Worker reference cache includes the audio hash and is bounded to four entries. RNG is seeded after prompt construction. Cached local model paths avoid an unnecessary Hugging Face tokenizer metadata lookup, verified offline.
- `tools/compare_prosody.py` uses the Qwen runtime Python and writes opt-in baseline/full-context/continuation WAVs and a JSON report under `data/comparisons`. It never enables continuity in Studio/MCP/API.
- ASR comparison on existing request #75 (35.29 seconds): old truncated input CER 0.2646; full input CER 0.0582 and recognition reaches the final sentence. This measures verifier accuracy, not an improvement to the synthesized audio. Details: `data/reports/asr-longform-20260924.json`.
- Quality findings, script guidance, limitations and comparison commands are documented in `docs/voice-quality.md`.
- Full regression: 29 tests passed. External API regression also checks that submitted engine/mode/generation/continuation overrides are ignored.
- Four actual comparison WAVs and `report.json` are in `data/comparisons/prosody-20260924-v2/`; `listen.html` provides a blind-label listening page. All six independently generated segments passed ASR/waveform checks.
- On the 216-character two-segment example, baseline absolute boundary pitch/RMS changes were 0.83 semitones/0.73dB; full-context 3.58/6.21; full-context plus continuation 1.82/6.46; original input mode plus continuation 0.17/3.34. Continuation improved pitch consistency on this example but increased the level jump. No experimental mode was promoted to production.
- All temporary comparison/Whisper processes exited. The production server remains offline and there are no queued/running requests or jobs.

## Continuity follow-up completed (2026-09-24)

- User asked to continue improving continuity after discussing the old 30-second ASR limit. Do not interpret 30 seconds as a TTS hard limit.
- Added opt-in `anchored` context in the Qwen worker: original reference acoustic codes followed by verified previous-segment codes, matching transcripts, and the original speaker embedding. No output gain adjustment, time stretching, or crossfade was introduced.
- Added `voicebench/continuity.py` and `tools/compare_continuity.py`. The tool reuses only matching, hash-verified, ASR-passed baseline segments. It compares original-reference acoustic anchoring and merging compatible segments within a measured 28-second/280-character planning budget. Longer-turn grouping remains comparison-tool-only; anchored context is now an optional Studio preset.
- Planning-only report: `data/comparisons/continuity-plan-20260924/report.json`. The 216-character example can be one estimated 25.28-second generation instead of two. Historical planning report: `data/reports/continuity-grouping-20260924.json`; #72 goes 42 to 40, #71 and #75 retain meaningful boundaries, #73 skipped because stored segment texts differ from the current planner.
- User replied **그래**, explicitly authorizing the temporary stop of the other project's GPU model process. Fresh identity verification matched exactly one `PersonalProject1/runtime/llama/llama-server.exe` (PID 35372), which was stopped. Parent application PID 21636 remains running; its settings and files were untouched. Its existing `models.ensure()` automatically reloads the configured model on the next inference request. Do not manually launch a second unmanaged llama-server: the parent tracks its own child and must perform the reload. No other process was stopped.
- Actual comparison completed successfully in `data/comparisons/continuity-20260924-verified/`. `listen.html` offers three blind-label players; `report.json` records exact settings and measurements. All new segments passed waveform/ASR checks. Comparison and Whisper processes exited; GPU usage returned to about 805 MiB with 7255 MiB free.
- Baseline: raw segments 15.68 + 9.60 seconds, CER 0/0, boundary pitch -0.83 semitones and RMS +0.73 dB.
- Anchored context: second segment 8.88 seconds, CER 0.0175 (one recognized character difference), no omission/repetition warning; boundary pitch +0.17 semitones and RMS +0.98 dB. This reduces the earlier replacement-context level jump of 3.34 dB and brings duration closer to the baseline. It does not beat baseline RMS or prove perceptual naturalness.
- Longer turn: all 216 characters in a single raw 24.16-second generation, CER 0.0, no omission/repetition warning. One generation removes the former join. This is a measured example, not a guaranteed 30-second generation limit.
- Registered `Qwen3-TTS 1.7B · 문맥 연결 비교 (실험·HQ)` with `tools/register_continuity.py`. It copies the current expressive settings and adds `continuity_mode=anchored`. Production engine row and job/request/run counts were verified unchanged. Fresh Qwen registration also includes this optional preset.
- `JobRunner` now supplies context only for the opt-in HQ preset. It checks the same request, paragraph, engine/config/reference/text, previous selected ASR result, 3–20-second duration and WAV hash. It resets at paragraph/transition/conclusion boundaries and after one hop. No ASR, changed context or invalid prior result means original reference only.
- Failed/ASR-unverified context or a worker generation error retries the original reference at the SAME Seed before normal Seed 10/42 fallbacks. Unverified context is excluded from final candidate selection even if it has a lower score. Metrics/manifests record continuity usage, reset/fallback reason, source job and selected attempt. Default Studio/external behavior, reference B and external Seed 13 remain unchanged.
- Full regression: **43 tests passed**, including 8 new runner/payload tests for opt-in gating, one-hop reset, context identity, same-Seed fallback, ASR unavailability, generation errors and failed-context exclusion. `git diff --check` passed. No commit was made.

## Detailed naturalness diagnosis (2026-09-24)

- User says outputs still sound strongly AI-like and requested detailed analysis. Analysis report: `docs/naturalness-analysis-20260924.md`. No production code/settings/reference/Seed changes were made during this analysis.
- Verified existing requests #71–#75: 125 raw segments, all expressive/default Seed 13 with `passed` quality status, no continuity use. Across their 120 boundaries, 87 planned pauses are 280ms. C remains opt-in and external/default output does not automatically use it.
- Main confirmed gaps: semantic roles only drive segmentation/pauses/context resets; expressive preset only changes sampling; Base voice-clone receives no explicit performance/emotion plan; a single 9.95-second reference is used without owner-specific fine-tuning; QA ranks correctness/validity and stops at the first passing candidate, not perceptual naturalness.
- C is fresh generation conditioned on original + re-encoded prior acoustic codes, not continuous model-state carryover. It resets after one hop. In the latest blind sample, A and C reuse the exact same first 15.68-second WAV. C preference is evidence for prioritizing continuity, not proof the whole clip or long-form output is solved.
- Waveform audit found 0 clipping samples across those 125 segments; maximum energy removed by the current edge trim was about 0.001153%. Median leading/trailing removal was 0ms; p95 was 318ms/20ms. This weakens gross clipping/over-trimming as the main explanation but does not certify preservation of quiet breath/unvoiced sounds.
- Whole-clip autocorrelation pitch spans do not indicate a flat single pitch: A/C p10–p90 spans ~10.49/10.70 semitones. These are approximate diagnostics, not naturalness scores; reference and generated scripts differ. Meaning-appropriate placement of prosody remains unmeasured.
- Created a CPU-only FP32 codec round trip of the real reference using the cached speech tokenizer (no new TTS, no GPU use). Original 9.95s -> reconstructed 10.0s, 125×16 codes, 24kHz. Compare `data/reports/naturalness-audit-20260924/codec-listen.html`. This isolates encoding/decoding from predicted prosody but is not identical to production GPU FP16.
- Reference/codec Whisper CER was 0.0189/0.0566, no omission/repetition warnings. Small ASR differences do not establish codec naturalness or prove the spoken reference differs from its transcript. No claim of directly hearing metallic sound was made.
- Detailed measurements are in `data/reports/naturalness-audit-20260924/{waveform-audit,codec-roundtrip,reference-transcript-check}.json`. Audio remains local/ignored. All temporary CPU codec/Whisper processes exited. Production remains offline/idle.

## Single-generation shorts follow-up (2026-09-25)

- The active goal remains incomplete: improve the best practical naturalness and report verified results. User clarified that joining is not a fix for shorts produced in one generation. Treat that clarification as the primary scope for the next comparisons.
- `tools/compare_shorts.py` generated 12 new outputs: 3 scripts (216/214/204 characters, all one real default-planner segment) × expressive / Talker 0.85 / Subtalker 0.85 / both 0.85. Same reference, Seed 13, other sampling settings, model and FP16. All passed ASR/waveform checks; all raw/final WAV hashes reverified. Actual durations 24.06–26.96 seconds. No subjective winner has been established and no sampling preset was promoted.
- Report and blind listening page: `data/comparisons/shorts-20260925/{report.json,listen.html}`. Detailed conclusions: `docs/shorts-quality-20260925.md`. The listening page's A–D order differs per script and from the previous C experiment. An async user preference question is pending; do not ask for permission again or declare naturalness from CER.
- Studio now uses authenticated, read-only `/api/studio/preview` with the real planner; shows actual generated segments/joins; guards stale browser responses; displays the selected engine instead of always showing the default. Studio and external request status expose actual continuity counts. Existing engine/reference/Seed/HQ generation defaults are unchanged.
- Python full regression: 48 passed. JS input-handler functional tests: `node --test tests/test_studio_ui.cjs`, 4 passed. JS syntax and `git diff --check` passed. After an initial connection failure, actual browser verification succeeded on a separate read-only loopback fixture: 216-character sample = one generation/no join; duplicated sample in two paragraphs = two generations/one join; expanded segment texts and selected engine banner matched. Screenshot layout also inspected. Fixture server session 24228 exited 0 and its temporary tab was closed.
- Synthesis exec session 29279, regression session 14570 and temporary UI fixture session 64366 all returned exit code 0. No synthesis is still running. GPU returned to about 890 MiB used / 7170 MiB free. Production server remains offline and jobs/requests have no queued/running rows. No production jobs or presets were changed; no commit.
- The comparison fingerprint records the source at launch. Later changes to preview/reporting legitimately make `--resume` with that old fingerprint fail; the experiment is already complete, so do not overwrite or regenerate it solely for that mismatch.

## Single-generation input-mode follow-up (2026-09-25)

- Added `--suite input_mode` to the same shorts tool: False/True `non_streaming_mode`, same production sampling/voice/Seed/script/one-segment plan. Six new outputs completed in `data/comparisons/shorts-input-mode-20260925/`. All raw/final hashes and QA results verified. No input mode was promoted to production.
- Existing input vs full-text input, duration/CER: story 24.16s/0% vs 24.63s/0.65%; explanation 25.20s/2.70% vs 24.61s/2.03%; conversation 26.00s/2.04% vs 24.16s/4.08%. Mixed ASR results do not establish a naturalness winner. Same-source sampling settings and real single-segment planning are covered by the 48th Python test.
- `listen.html` in each comparison folder has its own labels. Existing sampling audio order and labels were verified unchanged when feedback export was enhanced. Exported notes include the experiment fingerprint; browser note storage is isolated by that fingerprint. The two generated feedback scripts passed syntax and export/storage isolation checks.
- Comparison session 23435 and full regression session 29020 both exited 0. Temporary UI fixture session 24228 exited 0. GPU returned to about 874 MiB used / 7186 MiB free. No live comparison remains.
- User perceptual feedback is still pending from the prior async question. There is no verified subjective preference on any new single-generation experiment. This is the remaining acceptance decision; more automatic parameter sweeps or CER comparisons cannot substitute for hearing whether these clips sound less AI-like and preserve the owner's voice. Do not report the broad improvement goal achieved or silently switch the default.

## Acceptance audit / feedback dependency (2026-09-25)

- The same missing perceptual evidence has persisted across three consecutive goal turns: after the sampling comparison, after the input-mode/UI follow-up, and the current revalidation. No new user preference or exported feedback file has arrived in the workspace.
- Current-state revalidation confirmed both reports completed, all 18 raw/final WAV hashes matched, every candidate retained a usable ASR result without a retry flag, production queue was idle, and the selected reference, expressive generation settings and external Seed 13 were preserved. Existing Python 48-test / JS 4-test / real-browser verification covers implementation correctness, not perceptual naturalness. `git diff --check` passed again.
- Safe independent implementation and the controlled comparisons are finished. The requested perceptual improvement remains unverified, so the full goal is not complete. The next decision needs a listener's preference and voice-similarity judgment; generating more unjudged variants would not resolve that dependency. The goal is being marked blocked awaiting that information, not paused or achieved.
- When the user replies, interpret the precise comparison page and script before mapping labels. Previous continuity C, sampling A–D, and input-mode A/B are separate experiments. Use `report.json` plus the deterministic page mapping/export fingerprint. Apply a supported result without asking for duplicate permission, then validate actual production output and update the report before completion.

## Story A accepted and single-generation input adopted (2026-09-26)

- Owner's exact feedback: “스토리에서 A가 더 자연스러워”. Confirmed page is `shorts-input-mode-20260925/listen.html`, story A = full_text, NOT sampling A–D or continuity C. Experiment fingerprint `52b2569a7df93f2b9ec89880a6cb56929055f78f61f2b82c98e413156322b844`; feedback and selected WAV hash saved in `data/comparisons/shorts-full-text-adoption-20260926/owner-feedback.json`.
- Implemented `single_segment_full_text_v1` in the shared JobRunner: default expressive command worker + HQ + one segment in the ENTIRE request + no explicitly configured input mode -> copied generation config with `non_streaming_mode=True`. Uses actual plan count, not duration. Other sampling settings, reference and primary Seed unchanged. Multi-segment/long-form, standard, comparison engines, benchmarks and explicit input modes are preserved. Registered engine rows are not modified.
- All QA retry candidates retain this input mode. Run metrics/manifest `synthesis` records effective generation, policy and total request segment count; `manifest.engine.config` remains the registered config. External fingerprint version is 6 and includes server input policy, so old cached results are not reused.
- Real GPU verification completed through external Service creation -> real runner -> actual Qwen worker -> actual CPU Whisper -> final WAV, using a separate local DB/output/port 8792. Production engine/reference were read only; no production request was added and no LAN server was started. HTTP routing/auth and ignored caller overrides are covered by regression tests, not the GPU probe.
- New output is 24.63s, Seed 13, first attempt passed, CER 0.0065, no omission/repetition warnings. All effective generation settings and reference match selected A. Raw/final frame counts match A but hashes differ; final PCM16 RMS difference 0.603, max 16, relative difference energy -69.66dB. Do not claim bit-identical synthesis or an additional owner vote on the newly generated file.
- Result: `data/comparisons/shorts-full-text-adoption-20260926/story-full-text-service.wav`; detailed evidence `verification.json`; one-off reproduction source `data/verify_short_full_text.py`. Probe exec session 47585 exited 0 and closed its own Qwen/Whisper workers. The output directory is intentionally unique; the script refuses to overwrite it.
- Python regression 53 passed; Node UI 4 passed; `git diff --check` passed. Five added tests cover actual worker payload web/external parity, total count with one remaining long-form job, mode/comparison/config exclusions, QA retries and policy-sensitive deduplication.
- Owner preference is now verified for all three tested scripts: story, explanation and conversation favor A. Consolidated exact quotes, experiment fingerprint and audio hashes: `data/comparisons/shorts-full-text-adoption-20260926/owner-feedback-three-scripts.json`. The original story-only feedback file remains a historical record. This supports retaining the selected single-generation policy; it does not establish superiority for every script or multi-segment generation. Earlier feedback-blocked entries are historical.
- At completion of the isolated verification, production remained offline/idle. The owner subsequently requested startup; current operating state is recorded at the top of this file. No commit, no preset DB update, no GPU/process interference with other projects. Existing dirty worktree preserved.

## New-session startup

- On 2026-09-26 the owner explicitly requested committing and pushing this session's code, tests and documentation to the existing `origin/main`. Earlier “no commit” notes describe the state before that authorization. Local recordings, experiment artifacts, credentials and runtime files remain excluded by `.gitignore`; inline chat players also remain local. Read Git history/status for the publication state instead of treating the earlier notes as current.

- No special startup prompt is required. Root `AGENTS.md` instructs every new session to read this file completely, preserve the dirty worktree, and inspect Git/server/queue state before acting.
- Pinokio and Gepeto remain prohibited for this project.
