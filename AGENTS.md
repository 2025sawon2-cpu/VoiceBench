# VoiceBench workspace instructions

## 협업 규칙 읽기

협업 규칙의 원본은 [docs/COLLABORATION.md](docs/COLLABORATION.md)다. 협업 절차는 이 문서를 따르며 아래의 일반적인 커밋/push 대기 지침보다 우선한다.

작업 시작, 세션 재개·컨텍스트 압축 후, 수정 범위 확장 시, 병합/push 직전에 반드시 원문을 다시 읽는다. 기억이나 대화 요약으로 대신하지 않는다.



These instructions apply to the entire repository.

## Installation intent: “설치해” / “install” / first clone

When the user asks to install this repository, complete the full first-run workflow;
do not stop after bootstrapping Python or installing only Qwen.

1. Read this file and the session handoff, preserve local edits, then follow
   `docs/windows-install.md`. Historical machine settings in the handoff are not
   requirements for a new PC. Do not request the previous owner's private voice.
2. Run `setup-windows.cmd -Plan` when Python is available to inspect the actual PC.
   The default full command is **`setup-windows.cmd`**; it installs all five TTS
   families, Qwen Base 0.6B/1.7B and CustomVoice, plus Whisper. `-DownloadModels`
   remains a compatible alias; model download is now always included.
3. If this checkout is already running, inspect its live server identity and all
   queued/running requests before any restart. Wait for busy work; do not kill
   another application's process or silently cancel jobs. The installer refuses
   occupied ports or busy queues. Stop only verified owned idle services when
   needed to carry out the authorized installation.
4. Let setup choose the GPU, CUDA wheel, ZONOS2 quantization and native build SM.
   Never assume SM89 or this owner's GPU on a fresh clone. Unsupported hardware,
   failed downloads, CUDA tests, synthesis or QA must be reported as incomplete.
5. Continue through the actual Korean preview preparation. All **22 expected
   previews** must pass for a complete installation; engine registration alone
   is not evidence of working synthesis. Retry actionable failures using the
   same command; cache and private settings are preserved. Do not enable a
   deliberately disabled engine merely to make the completion count pass.
6. Open the returned **http://127.0.0.1:8765/welcome.html** and verify the page,
   generated sample players and cloning instructions. Autoplay may need one
   user click; direct them to “전체 샘플 듣기” if blocked by the browser.
7. End with the clickable local URL and a concise completion/remaining-failure
   statement. Explain that “목소리 보관함” accepts a clean 3–30 second recording
   and its accurate transcript. Existing personal defaults, API keys, seed and
   quality settings stay intact. Do not publish LAN access or private recordings
   as a side effect of installation.

For implementation-only requests, validate with isolated fixtures and tests;
do not claim a clean-PC model download/GPU synthesis run unless it was performed.

## Mandatory session startup

At the beginning of every new session in this workspace, before taking task actions:

1. Read `SESSION_HANDOFF.md` completely.
2. Inspect `git status --short` and preserve all existing modified and untracked files.
3. Check whether the VoiceBench server is running and whether any TTS request is queued or running before restarting or stopping processes.
4. Continue from the documented state without asking the user to repeat the project background or paste a startup prompt.

## Project constraints

- Do not use Pinokio or Gepeto for this project.
- The target is the best practical local voice-cloning TTS quality on the owner's PC.
- Treat the selected reference voice, current ZONOS2 Q8/CUDA default, fixed external Seed 13, and long-form HQ behavior as intentional unless the user explicitly requests a change.
- Preserve the dirty worktree. Never reset, discard, or overwrite existing changes merely to simplify the task.
- Do not write web passwords, API keys, or other secret values into source files, documentation, logs, commits, or responses unless the user explicitly requests a one-time display from the local secret source.
- Web Studio comparison controls may remain adjustable. MCP/external API engine, reference, quality mode, and Seed are server-controlled; supplied external `seed` values must be ignored.
- The owner explicitly enabled named Qwen CustomVoice and ZONOS2 official voices for Hotpost. External clients may select an approved `voice_id` from `/v1/voices`; its engine and reference are resolved by the server. Omission preserves the configured personal default. Arbitrary engine/reference/generation overrides remain prohibited.

## Verification

For code changes, run the relevant tests. The normal full regression command is:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Run `git diff --check` before completing implementation work. Do not commit unless the user asks.
