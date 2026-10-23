# VoiceBench workspace instructions

## 2026-10-23 사용자 확정 AI 협업 규칙

- 시작 전에 `docs/COLLABORATION.md`를 읽고 따른다. 두 개발자·두 PC·복수 AI 에이전트가 저장소 전체에서 병행 개발한다.
- AI가 작업 이슈를 생성·갱신·종료하고 작업별 브랜치를 사용한다. 수정 파일은 늘어날 수 있으며 범위 확장과 API/DB/공통 동작 충돌을 공유·조율한다.
- 의미 있는 기존 내용·기능·자료 제거는 무엇이 없어지는지와 영향을 설명하고 명시적 동의를 받는다. 이미 승인한 같은 제거는 재확인하지 않으며 상대 변경을 통째로 버리는 충돌 해결은 금지한다.
- 작업 완료 후 별도 push 명령을 요구하지 않는다. 최신 origin/main 병합·관련 검사·일반 push·원격 반영 확인 후 이슈를 종료한다. PR/별도 PR 리뷰는 필수 절차가 아니며 main 강제 push는 금지한다.
- AI가 구현·검증 완료를 판단하면 완료 여부나 push 승인을 다시 묻지 않고 자동 반영한다. 이 최신 답변은 앞선 매번 완료 확인 절차를 대체한다. 자동 push가 제작/수집/예약/배포 재개를 허용하지 않는다. 기존 미커밋 변경과 개인 자료를 보존한다.
- 이 사용자 명시 합의는 아래의 일반적인 커밋/push 대기 지침보다 우선한다.


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
