# Claude ⇄ JARVIS bridge: implementation plan

Self-contained: a fresh Claude Code session (or you) on any machine can pick this up from this file alone.
Written 2026-09-24 on the Windows desktop. Linux is the next target.

## Goal

Long term, replace the VS Code Claude Code session with a `claude` CLI session driven from the JARVIS dashboard
widget and by voice. Same session, mirrored screen, typed or spoken input, answers read aloud by JARVIS.
Immediate goal: **make it work on Linux first** (the next dev machine is a Linux laptop).

## Step 0: before anything else (on the Windows machine)

The Windows work is **uncommitted** as of this writing. Commit and push it, or the Linux machine won't have it.
Files involved: `CLAUDE.md`, `clJarvis.py`, `src/clClaudeBridge.py`, `src/ui/clSettingsWidget.py`,
`src/utils/clPtyBridge.py`, `src/utils/clClaudeStopHook.py` (new), `tests/test_clClaudeBridge.py`,
`tests/test_clClaudeSpeech.py` (new), `docs/claude_bridge_plan.md` (new).
Do NOT commit `config/core.json` (unrelated runtime drift such as `last_light_target`).
Sign-in lives in `data/claude_bridge_config/` (gitignored), so every machine runs its own `--setup` once.

## How it works today (Windows)

```
voice ─► clDaemon (regex route) ─► MQTT jarvis/claude/question {"text": ...} or {"keys": ...}
      ─► clClaudeBridge.py ─► ClaudePtyBridge (ConPTY via pywinpty, screen resolved by pyte, 120x40)
      ─► real `claude` TUI session (persistent, --continue once <config>/projects/ exists)
screen ─► jarvis/claude/screen (retained, {"text": ...}) ─► future dashboard widget
answer ─► Stop hook (src/utils/clClaudeStopHook.py) ─► jarvis/claude/reply
       ─► bridge speakable_text() ─► jarvis/sys/speak {claude_session: true, request_reply: <ends in "?">}
```

Key behaviours already built and tested (699 tests green on Windows):
- Isolated config dir `data/claude_bridge_config/` via `CLAUDE_CONFIG_DIR` (sharing `~/.claude` with another live
  session hangs login: claude-code issues #91987, #63007).
- `CLAUDE_CODE_*` env vars stripped from the spawn env (otherwise transcript saving is off and `--continue` has nothing).
- `--continue` only when `<config>/projects/` exists (it hard-errors otherwise).
- First-run screens auto-answered from an `AUTO_ANSWERS` table (marker text on screen -> keys). The trust-folder menu
  needs Down + Enter; theme, login-method, and "Press Enter to continue" need plain Enter. Dropped Enters retry up to 3x.
- `python src/clClaudeBridge.py --setup`: one-time sign-in; opens the browser URL, exits when the idle prompt shows.
  The bridge parks itself until `.credentials.json` exists.
- Readiness = "at idle prompt" (a `❯` row bracketed by `─` rows) and quiet for 1s. Input is gated on it.
- Stall watchdog: busy ("esc to interrupt" on screen) plus 60s without output -> kill, restart, re-send the last question
  once. Skips the re-send if the reply was already delivered (fixed 2026-09-24: it used to answer twice).
- Speech is the bridge's job. A `PreToolUse` hook (`--block-speak`) blocks the session from running the repo's own
  speak command (would double every answer). `--append-system-prompt-file` tells the session the host reads answers aloud.
- Screen payloads are trimmed (rstrip rows and trailing blank rows) and published retained.

Files: `src/clClaudeBridge.py` (service, speech, stall recovery, `--setup`), `src/utils/clPtyBridge.py` (Windows PTY
wrapper), `src/utils/clClaudeStopHook.py`, tests in `tests/test_clClaudeBridge.py` and `tests/test_clClaudeSpeech.py`.
`_ensure_pty_started` currently logs "Linux/tmux backend not implemented yet" on non-Windows.

## Phase 1: Linux backend (do this first)

### 1a. Refactor: split backend I/O from shared logic
Today `ClaudePtyBridge` mixes ConPTY I/O with logic that is OS-independent. Extract a base class
(e.g. `src/utils/clClaudeSession.py`) holding: busy / prompt-visible detection, `is_ready`, `AUTO_ANSWERS` matching and
retry, stall detection, sign-in URL opening, `_write_session_files` (hook settings + prompt), env stripping, config dir
and `--continue` logic. Keep only these per backend: spawn, send text, send raw keys, obtain screen text, alive check, stop.
Express `AUTO_ANSWERS` keys as backend-neutral tokens (`"DOWN ENTER"`); the Windows backend maps them to `\x1bOB\r`
(application cursor mode), the tmux backend to `send-keys Down Enter`. Keep Windows tests green; move shared tests to the base.

### 1b. tmux backend (`ClaudeTmuxBridge`)
tmux resolves the ANSI screen itself, so **pyte and pywinpty are not needed on Linux**.
- Preflight: `tmux` on PATH, else log a clear "install tmux" message and park like the missing-login case.
- Spawn: `tmux new-session -d -s jarvis-claude -x 120 -y 40 -c <repo> -e CLAUDE_CONFIG_DIR=<dir> <claude> [--continue] --settings <f> --append-system-prompt-file <f>`
  (use `env -u` or a filtered environment for the `CLAUDE_CODE_*` strip). Resolve the binary with `shutil.which("claude")`;
  the `.cmd` shim problem is Windows-only.
- Attach instead of respawn: if `tmux has-session -t jarvis-claude` succeeds, reuse it. This gives **crash resilience for
  free**: the session outlives the bridge process (see Phase 3).
- Send text: `tmux send-keys -t jarvis-claude -l "<text>"`, short pause (~80ms), then `send-keys Enter`. The `-l` flag is
  literal mode, so words like "Enter" in the text aren't parsed as keys.
- Send raw keys (the `keys` payload): map to tmux key names. Do not send escape bytes; tmux handles application cursor mode.
- Screen and change signal (event-driven, no polling, per the resource-efficiency rule): `tmux pipe-pane -O -t jarvis-claude
  'cat >> <fifo>'` and block on the FIFO. On activity, debounce ~150ms, then `tmux capture-pane -p -J -t jarvis-claude`.
  `-J` joins wrapped lines, which also makes the wrapped sign-in URL trivial to extract. Alternative if the FIFO is awkward:
  `tmux -C attach` control mode emits `%output` events.
- Busy/idle/stall: reuse the shared logic on the captured text. Output-silence tracking uses pipe-pane activity times.
- `stop()`: `tmux kill-session -t jarvis-claude`.
- Remove the non-Windows guard in `_ensure_pty_started`; choose the backend by `CURRENT_OS`.
- `requirements.txt`: confirm `pywinpty` and `pyte` are marked `sys_platform == 'win32'` so Linux doesn't install them.

### 1c. Linux verification checklist
- Run `python src/clClaudeBridge.py --setup`. Check the browser opens (`xdg-open` via `webbrowser`) and the Authorize step works.
  The `Paste code here` handling is still untested against a real sign-in on either OS.
- Fresh start: trust menu answered, `--continue` omitted first run and used afterwards.
- Voice question -> answer spoken once. An answer ending in "?" reopens the mic (`request_reply` path is untested live).
- Stall recovery: kill the child claude process, watch restart and the single re-send.
- Tests: mock `subprocess` for tmux calls (same style as the Windows tests), plus the shared-logic tests. Every new
  behavior gets tests in the same pass.

## Phase 2: reliability

- **Stop-hook delay / false stalls.** On 2026-09-24 the TUI sat on "running Stop hook" for 60s+ after the reply was already
  published. Find out why (does the hook process exit? does `paho publish.single` block?). Try `publish.single(..., keepalive=...)`
  timeouts, or a fire-and-forget hook. The watchdog can't tell a stuck hook from a stuck model; for a dev session, a false
  restart can destroy in-progress work. Consider a longer stall window whenever the screen shows "running Stop hook".
- Live-verify the untested paths listed above.

## Phase 3: crash resilience

- Linux: already covered by tmux (Phase 1b attach logic). Test: kill the bridge process, restart it, confirm the same
  conversation is still alive and mid-turn work continued.
- Windows: needs a detached session-host process that owns the ConPTY, with the bridge talking to it over a local socket.
  Do this after Linux works.

## Phase 4: dashboard widget (`ClaudeTerminalWidget`)

Backend first, then this (user decision). Design notes:
- Model it on `src/ui/clLogWidget.py` / `ZoomTextEdit`.
- Add a signal on `MqttThread`, subscribe to `jarvis/claude/screen` in `on_connect`, handler in `JarvisUI`.
- Cache the latest screen (the topic is retained, so a late subscriber gets it).
- Add a `WidgetTogglePill` and `_toggle_claude` with widget id `widget_claude`; update the show/hide lists in
  `set_fullscreen` / `set_overlay` and the restore branch in `load_ui_state`.
- Widget size is owned by `ui_state.json` (see the `widget-size-source-of-truth` memory); never `adjustSize()` the content.
- Input: a prompt box publishing `{"text": ...}`, plus buttons/keys for Esc (interrupt), Shift+Tab, arrows, Enter
  publishing `{"keys": ...}`.
- Permission prompts must be visible and answerable from the widget (today the session runs in auto mode).
- Scrollback: the 120x40 grid only shows the current screen. Publish scrollback (tmux `capture-pane -S -`) or keep a
  history in the widget.

## Phase 5: replacing the VS Code session

- **Config-dir cutover.** The isolated dir has no memories, user settings, or MCP servers. To make the bridge the main
  session, point `CLAUDE_CONFIG_DIR` at the real `~/.claude`, but only when no other `claude` session is using it
  (lock-contention hang). So the cutover is all-or-nothing, not side by side.
- Keep the repo as the working directory (a dev session needs its `CLAUDE.md` and tools).
- **Token cost.** It runs on the account subscription (not an API key). Cost is dominated by the fixed per-turn overhead
  (system prompt, tools, `CLAUDE.md`) plus a growing history, mostly served from prompt cache. Levers: a configurable
  `--model` in config (Opus/Sonnet for dev, Haiku for quick voice questions), a "new conversation" voice command that
  drops `--continue`, and letting the CLI auto-compact. Measure first: read real usage from the session transcripts in
  `<config>/projects/`.
- Make session model and permission mode configurable in `config/core.json`, with per-machine values.

## Conventions to follow (from CLAUDE.md and prior feedback)

- Concise one-line code comments. Match the surrounding style.
- Prefer event-driven design over polling; measure real resource impact.
- Cross-machine parity by default; Windows uses native code paths, not WSL.
- Add unit tests for every new behavior in the same pass.
- After changing a module, restart it so the change is live.
- Give commit messages only; do not run `git commit`.
- Warn before any audible test.
- When the ecosystem is live, end each turn with a one-sentence spoken summary via `jarvis/sys/speak` (except inside the
  bridged session itself).
- Windows-only gotchas: each service is two python processes (launcher stub plus child); kill by script name with
  `taskkill /PID <stub> /T /F`, and confirm which process you're killing (killing the supervisor's stub takes everything down).

## Open decisions

- Should bridge speech ignore silent mode? Currently it respects it.
- Trust prompts and permission mode for the dev-session role (auto mode vs. approving in the widget).
- Whether voice questions and the dev session share one session or get separate ones.
