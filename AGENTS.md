# VoiceBench workspace instructions

These instructions apply to the entire repository.

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

## Verification

For code changes, run the relevant tests. The normal full regression command is:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Run `git diff --check` before completing implementation work. Do not commit unless the user asks.
