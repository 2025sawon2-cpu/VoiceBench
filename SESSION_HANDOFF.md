# VoiceBench session handoff

Updated: 2026-09-26 (Asia/Seoul)

This file is the starting point for the next Codex session. Read it before making changes. Do not use Pinokio or Gepeto in this project.

## Current goal

Run the best practical local voice-cloning TTS service on the owner's PC, using the owner's fixed reference voice. The web UI and LAN MCP/API should produce the same speech when the script, engine, seed, and quality mode are identical.

Latest owner feedback: after selecting story A, the owner also said “설명형 대화형 둘다 a가 더 좋아.” All three tested scripts now favor A = `full_text` (`non_streaming_mode=True`) in the **input-mode A/B** experiment, `data/comparisons/shorts-input-mode-20260925/listen.html`. These labels are unrelated to sampling A–D or continuity C. Explanation/conversation were delivered as lossless inline chat players, with source WAV hashes and decoded PCM checked. The September 25 missing-feedback audit below is historical. Keep the already implemented single-generation full-text policy; no further code change or server restart was needed for this feedback.

## Current operating state

- Workspace: `C:\Users\s98se\OneDrive\Pictures\Documents\ChatGPT\인스타_자동화\VoiceBench`
- Local URL: `http://127.0.0.1:8765`; LAN hosting remains offline.
- At the September 26 pull, port 8765 was owned by PID 71172 and the model worker on 8790 by PID 68296. The database had no queued/running jobs. These processes predate the merge; pull/review does not itself reload them.
- Runtime credentials remain private; use the authenticated hosting script for LAN operation. `.runtime/hosting-session.json` records this machine's local server.
- The remote September 26 startup/#76 evidence below comes from another checkout (`C:\Users\alseo\Documents\Voice`, GTX 1070 Ti, original launch PID 30448). Its process IDs, data files and runtime state must not be assumed to exist here.
- The old ngrok public URL is intentionally offline.
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
- Hardware: RTX 4070 Ti SUPER 16 GB; the 1.7B worker runs FP16/SDPA.
- Latest VoxCPM2 preference: no emotional/style instructions. User rejected even the reduced "20%" attempt as too agitated; both Shorts presets are disabled. Use the original reference-audio-plus-transcript continuation preset (`voxcpm2-experimental`, CFG 2.0/10 steps). Do not re-enable emotion presets without a new user request.

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

The merged September 26 regression passed 64 Python tests and 5 Node UI tests.

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

## 2026-09-24 SDPA default

- The 1.7B engine presets use FP16/SDPA for both the persistent worker and one-shot adapter. The 0.6B comparison engine retains eager attention.
- Worker health now reports attention, and engine startup checks it before reusing an existing worker.
- Two matched-seed comparisons on this PC favored SDPA by 6.7% and 10.9% in real-time factor. The sample is small and does not establish perceptual quality superiority. FlashAttention 2 was unavailable in the Windows runtime.

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

## 2026-09-24 Chatterbox Multilingual V3 comparison

- Researched current open-source Korean-capable voice-cloning models. Chatterbox Multilingual V3 was selected for a local Windows trial because its official code and selective weights could run in a separate environment. CosyVoice 3, Confucius4-TTS, and MOSS-TTS remain research candidates, not locally validated.
- Installed Chatterbox in `.runtime\engines\chatterbox-v3\venv` with Torch/Torchaudio 2.6.0+cu126 and official Git commit `5de7a54aa4e5e2baadb0182dde554908b48b85c2`. The PyPI 0.1.7 wheel has V2 only despite sharing the same version number; the pinned Git build supports `t3_model="v3"`. Selected weights were cached under `.runtime\cache\huggingface`.
- Same 221-character Korean prompt and selected personal reference: Qwen 1.7B FP16/SDPA Seed 13 produced 27.28 seconds in 41.589 seconds, RTF 1.525, peak allocated 4759 MB, Whisper Base CER 0.0429. Chatterbox V3 Seed 13 produced 24.8 seconds in 21.491 seconds, RTF 0.867, peak allocated 3812 MB, CER 0.2025; Seed 42 produced 23.24 seconds in 12.178 seconds, RTF 0.524, CER 0.1288. Both Chatterbox outputs missed the exact final 4-character ASR check. These are limited single-prompt results, not a perceptual ranking.
- Chatterbox's own voice-encoder cosine against the selected reference was 0.9576 for its Seed 13 output and 0.9235 for Qwen's. This is potentially biased toward Chatterbox; listen to the files before judging cloning quality.
- Comparison WAVs: `.runtime\benchmarks\sdpa.wav`, `chatterbox-v3.wav`, and `chatterbox-v3-seed-42.wav`. Matching JSON metrics are beside them. These are private and Git-ignored.
- Added optional `chatterbox-multilingual-v3-experimental` Studio engine via `adapters\chatterbox_v3.py` and `tools\register_chatterbox_v3.py`; registered locally as engine ID 29. It is a one-shot command engine, so each long-form segment reloads the model. The production Qwen expressive default and external API controls remain unchanged. The registered engine and direct adapter both passed Korean synthesis smoke tests.
- Full regression suite: 19/19 passing via `python -m unittest discover -s tests -v`; `git diff --check` passed. `pytest` is not installed in the application venv, so use the documented unittest command.

## 2026-09-24 VoxCPM2 integration and trial

- Added `voxcpm2-experimental`, locally engine ID 36, selectable in both Studio and the benchmark page. Production Qwen expressive/Seed 13 and external API defaults are unchanged.
- Reproducible installer: `install-voxcpm2.cmd` / `.ps1`, `tools/download_voxcpm2.py`, and `tools/register_voxcpm2.py`. Dedicated environment `.runtime/engines/voxcpm2/venv`: Python 3.10, VoxCPM 2.0.3, Torch/Torchaudio 2.6.0+cu126, Transformers 4.57.6, NumPy 1.26.4, Gradio 6.17.3. `pip check` passed.
- Model snapshot `openbmb/VoxCPM2` revision `32279effe8c19989596f05d353d1447f51d9e915` is cached locally (~5GB). Personal reference audio stays local; workers run with `HF_HUB_OFFLINE=1`.
- `adapters/voxcpm2_worker.py` uses official BF16/SDPA, CFG 2.0, 10 diffusion steps, reference WAV plus transcript, 48kHz PCM output. `torch.compile`, denoising, text normalization, and internal badcase retries are disabled. PyPI 2.0.3 has no `generate(seed=...)`, so Python/NumPy/Torch RNGs are seeded explicitly under the generation lock.
- Persistent worker shares port 8790 with Qwen. Model switching in both directions and same-model reuse were verified through real queued jobs. VoxCPM2 does not receive Qwen-specific Whisper QA retries; the UI explicitly identifies this distinction.
- Saved comparison project ID 2, `VoxCPM2 vs Qwen · 한국어 비교 2026-09-24`, contains the same 221-character Korean prompt and reference: run 2 Vox Seed 13 (27.36s audio, 46.2082s synthesis including reference encoding, RTF 1.6889, 6251.2 MiB allocated, Whisper Base CER 0.0307); run 3 Vox Seed 42 (26.40s audio, 27.9713s synthesis, RTF 1.0595, 6220.1 MiB allocated, CER 0.0552); run 4 Qwen Seed 13 (27.28s audio, CER 0.0429). All matched final ASR suffix and passed waveform checks.
- Total queue execution times were 68.47s / 28.43s / 90.48s respectively. They include differing loading/model-switch overhead and are not comparable as pure generation speeds. Vox's first vs second inference also mixes warm-up and Seed differences. This is a single-prompt integration trial, not evidence of overall model superiority or a listening assessment.
- Actual browser Studio request ID 2 (8.99s final audio) succeeded using VoxCPM2. Benchmark UI shows all three completed results. The user can use the existing blind-comparison page for listening.
- Worker measurements are saved in run manifests and selectively exposed under `metrics.engine_performance`. UI now distinguishes total time, synthesis time, model peak allocated memory, total GPU usage, and memory increase; resident models previously appeared to use 0MB when only the delta was shown.
- Private evaluation files: `.runtime/benchmarks/voxcpm2-results.json`, `voxcpm2-run-{2,3,4}.quality.json`, and `VOXCPM2_RESULTS.md`. Final regression suite 23/23; `git diff --check` passed. No commit created; preserve all prior dirty changes.

## 2026-09-24 expressive Shorts presets

- User found the neutral Vox/Qwen comparison too much like reading a book and requested more excited Shorts delivery. Added selectable `voxcpm2-experimental-bright` (engine 44) and `voxcpm2-experimental-energetic` (engine 45). They use official Controllable Cloning (`reference_wav_path` plus a parenthesized style description), not continuation cloning with the reference transcript. The original default, CFG 2.0, 10 steps, and external API remain unchanged. No pitch/time-stretch postprocessing.
- Style is a validated engine generation option; it is recorded in manifests, and the original script remains unchanged in prompts. Continuation still requires the reference transcript. Controlled style cloning may reduce voice fidelity or intelligibility; do not promise the intended mood was achieved without listening.
- Project 3 (`아디다스 가방 · VoxCPM2 vs Qwen · Seed 13`) contains the exact user-provided 143-character script, all Seed 13: base Vox run/job 7 (19.68s, Whisper CER 0.0187); Qwen run/job 8 (19.2s, CER 0.0654); bright run/job 9 (18.4s, CER 0.0841); energetic run/job 10 (16.64s, CER 0.0654). Style outputs passed waveform checks and had no gross omission/repetition flags, but ASR error increased. No subjective listening verdict recorded.
- Files: `data/artifacts/outputs/project-3/prompt-5/job-{7,8,9,10}-*.wav`. Detailed private checks in `.runtime/benchmarks/adidas-comparison-results.json` and `adidas-expressive-results.json`.
- Current Qwen Base voice-clone interface has no direct natural-language emotional instruction equivalent. The earlier Qwen "expressive" preset only changes sampling parameters.
- Worker/request mapping and both preset selections are covered by tests; full suite 24/24. Server restarted after completion, no active jobs/model worker; local URL and hosting-session file remain available.

## 2026-09-24 user preference: subtle emotion

- User judged both Shorts style trials too emotionally heightened and asked for approximately 20% of that expression. Treat the desired direction as ordinary conversation with a little warmth, not enthusiastic promotional delivery. There is no calibrated emotion percentage control.
- Updated existing `voxcpm2-experimental-bright` (ID 44) to display `VoxCPM2 2B · 쇼츠 은은한 추천`; style prompt is `natural conversational voice, mostly neutral with a hint of warmth and a slight smile, restrained intonation, normal speaking pace`. CFG 2.0, 10 steps, Seed 13, reference and user script are unchanged. Earlier job 9's original stronger settings remain in its manifest even though the live engine name has changed.
- Project 3 job/run 11: 15.68s audio, Whisper CER 0.0467, ending matched, no gross omission/repetition flags. File `data/artifacts/outputs/project-3/prompt-5/job-11-voxcpm2-experimental-bright-seed-13.wav`; private check `.runtime/benchmarks/adidas-subtle-results.json`. User listening feedback is still needed to judge the intended emotional intensity.
- Relevant VoxCPM2 tests 5/5 and `git diff --check` passed. Server and latest VoxCPM2 worker remain running locally after this generation; no restart was needed for the preset configuration update.

## 2026-09-24 emotion experiment rejected

- User listened to the subtle sample and still found it far too agitated. They explicitly requested no added emotion. This supersedes the earlier expressive/subtle tone requests.
- Disabled engines 44 and 45 (`voxcpm2-experimental-bright`, `voxcpm2-experimental-energetic`); installer registration also keeps them disabled. Preserved all generated comparisons. The active VoxCPM2 engine remains original continuation cloning without a style prompt, CFG 2.0/10 steps. No new synthesis was needed.

## 2026-09-24 four-model comparison completed

- User explicitly requests maximum practical quality and four Adidas-script samples: VoxCPM2 / Qwen3 / Fish / ZONOS2. Long setup time is acceptable. No emotion instructions; same selected reference and primary Seed 13. Only VoiceBench may be changed. Four-model implementation, generated files, and objective checks are now complete; subjective naturalness/voice similarity remain for user listening.
- No Fish engine or cache was present. Added `adapters/fish_speech_worker.py`, `adapters/local_tts_worker.py`, `adapters/zonos2_worker.py`, `adapters/zonos2_speaker.py`, `tools/register_fish_zonos.py`, pinned downloads/installers, and `docs/four-model-comparison.md`. Engines 56 Fish, 59 Vox HQ20, 62 ZONOS2 base, 75 ZONOS2 diagnostic stable. Production Qwen unchanged.
- Fish S2-Pro original BF16 weights, 4096 context, T0.8/P0.8/K30, no style. Loading on a meta skeleton avoids random initialization; stage the model and codec on GPU rather than keeping both resident. Job 18 WAV is byte-identical to job 12 (SHA256 `a376d511ed8138e926b46942a665a5f11158d5ed0b4b8514b84dc966181e5361`), but GPU allocated peak falls 15894.6→9421.7MiB, synth 103.97→70.61s, model load 110.46→10.49s. Job 16 was deliberately stopped during old slow initialization and remains failed in history.
- Project 3 new good samples: job 13 Vox HQ20 19.68s/CER1.87%; job 14 Qwen FP16 SDPA 19.20s/CER6.54%; job 18 Fish 20.619s/CER4.67%. All end correctly, no clipping. Vox HQ20 is a comparison option, not proof of perceptual improvement over 10 steps. Detailed local checks: `.runtime/benchmarks/four-model-quality.json`; rerunnable private evaluator `.runtime/benchmarks/evaluate-four.py` with PYTHONPATH=root.
- ZONOS2 official Windows Vulkan v0.5.1 first failed on non-ASCII model paths, then on native CPU speaker encoder initialization (`0xC000001D`). The bridge now uses ASCII model filenames in a cwd with hardlinks and the **same official ECAPA encoder/preprocessing** through PyTorch FP32 CPU; pinned speaker model `marksverdhei/Qwen3-Voice-Embedding-12Hz-1.7B` revision `7577f61c42737fc8064bba773e2a18602df92803`.
- Vulkan now runs, but job 19 default sampling (37.88s/CER185.05%) repeats and omits, and job 20 conservative sampling (12.05s/CER91.59%) omits. These are FAILED QUALITY trials, not acceptable final samples. Q8 file SHA256 verified against HF: `42dbdc642936bee4c9b0ed3045eadd815aba2166038d41b9abd04d683e911fb4`. Upstream issue https://github.com/Zyphra/zonos2.cpp/issues/10 reports similar Vulkan language drift, substantially reduced on CUDA.
- **CUDA build completed successfully (177/177)**, log `.runtime/toolchains/build-zonos.log`, binary `.runtime/engines/zonos2/build-cuda/zonos2-server.exe`. Temporary V: mapping was removed after compilation finished. Reproducible `build-zonos2-cuda.ps1` performs the mapping/unmapping automatically.
- Portable MSVC14.44/SDK22621 and CUDA12.9.1 were prepared inside `.runtime/toolchains`, no global Visual Studio installation. CPU target AVX2, CUDA SM89, native tuning/AVX512 disabled. Original CUDA nvcc archive was read from existing LocalAppData installation; all new build artifacts are inside VoiceBench.
- ZONOS2 engine 62 now uses CUDA with T0.9/P0.95/minP0.05, accurate mode, no emotion. CUDA official sampling defaults still ended early (job 21, CER57.94%). Conservative job 22 and registered-preset job 23 both produced byte-identical 18.680s WAVs, CER4.67%, correct ending, no waveform clipping or omission/repetition warning. SHA256 `b4e0bad20c81af38a0ae5d3199160b32ab746f052c123600fccbeda83ab4bfff`. Job 23 synthesis 11.68s, excludes 14.54s model load. Use `data/artifacts/outputs/project-3/prompt-5/job-23-zonos2-q8-experimental-seed-13.wav` for final comparison. Diagnostic engine 75 is disabled; historical trials retained.
- Final samples are jobs 13 (Vox HQ20), 14 (Qwen), 18 (Fish), 23 (ZONOS2). All pass automated waveform/transcription checks; this single script was also used for tuning, so no general superiority or unseen-script robustness claim. No human listening assessment or independent speaker-similarity measurement performed.
- Live loopback server was found running as actual PID71172 (launcher8340); hosting-session server_pid corrected from stale29296. Actual `/api/studio` lists Fish, Vox HQ20, ZONOS2 with unchanged Qwen default. Jobs are terminal; ZONOS2 wrapper shutdown requested after final verification to release GPU and its owned native child. No need to regenerate comparisons at session startup.
- Full regression suite 29/29 passed. Fish `pip check` has the documented upstream descript-audiotools protobuf<3.20 conflict versus Fish's official >=3.20,<6 override; ZONOS2 env pip check was clean.
- F16 ZONOS2 download was attempted but failed after resumable transport errors; partial cache only, no running downloader. Do not claim F16 was tested.

## 2026-09-24 three-seed comparison completed

- User requested three different Seeds per model, with differences easy to compare. Created benchmark project 4, `내 목소리 · 4모델 × 3 Seed 비교`, prompt 9 copied exactly from prompt 5, and the same selected reference hash. Seeds 10/42/2026; unchanged per-model settings: Vox HQ20 (59), Qwen expressive SDPA (12), Fish BF16 (56), ZONOS2 CUDA/Q8 conservative (62). No emotion/style or automatic Seed replacement.
- Jobs 27–38 all succeeded, generated in 13m14s. Each model has three distinct full WAV hashes and three distinct first-five-second PCM hashes. All twelve passed Whisper Base ending/omission/repetition checks and have zero clipping. This verifies output differences and basic intelligibility, not perceptual distinctiveness or speaker similarity.
- Durations in Seed 10/42/2026 order: Vox 20.00/21.12/21.12s; Qwen 19.36/18.24/18.40s; Fish 19.64/19.60/19.92s; ZONOS2 19.17/19.48/16.93s. No candidate selection/cherry-picking; all requested Seeds are retained.
- Delivery folder `data/artifacts/seed-comparison-4/`: twelve clearly named full WAVs, four model-specific `*_seed-comparison-preview.wav` files, README and manifest. Each preview concatenates the unmodified first five seconds from Seeds 10/42/2026, separated by 0.8s silence (starts 0/5.8/11.6s). No loudness, pitch, speed or fade processing. Full copies are byte-identical to the original benchmark outputs. Verified bundle `data/artifacts/seed-comparison-4.zip` includes all deliverables.
- Private plan/quality/package scripts are in `.runtime/benchmarks/seed-comparison-*`, `evaluate-seeds.py`, and `package-seeds.py`. Application code and production defaults were not changed. Queue empty; ZONOS2 wrapper shutdown requested to release GPU after packaging. Local web server remains available.

## 2026-09-26 remote pull and integration review

- User requested pulling and reviewing new changes. Local `9678311` and remote `daba5d9` diverged from `7a1c64f`; fast-forward pull fetched the remote change but could not integrate it. A merge preserves both histories and the local Chatterbox/VoxCPM2/Fish/ZONOS2 integrations and SDPA settings.
- Resolved conflicts in this handoff, `voicebench/static/app.js`, and `voicebench/static/index.html`. Preserved model-specific quality banners together with the remote real-planner preview and stale-response guard. Machine-specific paths, GPU settings and running process records were reconciled for this checkout; remote experiment history remains historical evidence.
- Found and fixed an integration incompatibility: all three remote comparison tools called QwenWorkerServer without the attention argument introduced locally. They now use the registered attention setting, record it in reports/fingerprints, and reject reusing an eager baseline under SDPA. Old reports without this field mean eager, matching the old tool behavior. Regression tests cover attention-sensitive reuse and Qwen/Fish UI quality labels.
- Reviewed new full-text input policy for single-segment default Qwen HQ, opt-in anchored continuity, script-aware repetition checks, passing-candidate priority, numeric/semantic boundary fixes, complete long-audio ASR and reference-cache hashing. External Seed 13 and caller override restrictions remain intact. No local preset database updates or new synthesis were performed for this pull/review.
- Validation: Python 64/64, Node UI 5/5, JS syntax and comparison-tool constructor signature checks passed. No fresh GPU or listening evaluation on this PC; the remote input-mode listening evidence used a different machine/eager backend. Its Git-ignored recordings were not transferred by pull.
- At verification the queue was empty, local Qwen remained FP16/SDPA, and the optional continuity preset was not registered in this database. The live web server (PID71172) and Qwen worker (PID68296) still predate the merge. Both must be restarted before relying on new generation behavior; matching model/attention health alone does not detect an older worker source version. No running process was stopped and no remote push was performed during this review.
