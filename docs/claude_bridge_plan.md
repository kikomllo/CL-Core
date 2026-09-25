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

### 1a. Refactor: split backend I/O from shared logic — DONE (2026-09-24)
`src/utils/clClaudeSession.py` now holds `ClaudeSessionBase`: busy/prompt-visible detection, `is_ready`, `AUTO_ANSWERS`
matching and retry, stall detection (`_check_stall`/`_report_stall`), sign-in URL opening, `_write_session_files`,
`_strip_claude_code_env`, `_resolve_config_dir`/`_should_continue`. `ClaudePtyBridge` (`clPtyBridge.py`) now subclasses
it and keeps only: spawn (`start`), `write`, `send_keys`, `is_alive`, `stop`, and its own `_read_loop`/`_emit_screen`
(ConPTY polling + pyte). `AUTO_ANSWERS` tokens are backend-neutral (`"DOWN ENTER"`, `"ENTER"`); each backend implements
`_send_control_keys(token)` to translate — Windows maps to `\x1bOB\r`/`\r` via a small dict, tmux does `token.title()`
(e.g. `"DOWN ENTER"` → `"Down Enter"`, already a valid tmux key-name pair). All pre-existing Windows-specific tests in
`tests/test_clClaudeBridge.py` and `tests/test_clClaudeSpeech.py` pass unchanged; shared-logic tests moved to
`tests/test_clClaudeSession.py` (tested against a minimal concrete fake subclass, not a real backend).

### 1b. tmux backend (`ClaudeTmuxBridge`) — DONE (2026-09-24)
`src/utils/clClaudeTmuxBridge.py`. tmux resolves the ANSI screen itself, so pyte/pywinpty are never imported on Linux
(confirmed already `sys_platform == 'win32'`-marked in `requirements.txt`).
- Preflight: `start()` raises `FileNotFoundError` with a clear message if `claude` or `tmux` isn't on `PATH`.
- Spawn: `tmux new-session -d -s jarvis-claude -x 120 -y 40 -c <repo> -e CLAUDE_CONFIG_DIR=<dir> env -u <VAR> ... <claude>
  [--continue] --settings <f> --append-system-prompt-file <f>` — `env -u` (one pair per inherited `CLAUDE_CODE_*` var)
  strips them from the pane's process env even against an already-running tmux server whose own environment predates
  this launch (a plain `subprocess.run(env=...)` wouldn't reach that case).
- Attach instead of respawn: `start()` checks `tmux has-session -t jarvis-claude` first and reuses it, giving crash
  resilience for free (Phase 3) — a bridge-process restart never touches an already-running session.
- `write()`: `tmux send-keys -t jarvis-claude -l "<text>"`, ~80ms pause, then `send-keys ... Enter`.
- `send_keys(keys)`: space-separated tmux key names (e.g. `"Down Enter"`), passed straight to `send-keys` — no escape
  bytes, tmux resolves application-cursor-mode itself. This is also what `_send_control_keys` feeds it.
- Screen + change signal: `pipe-pane -O -t jarvis-claude 'cat >> <fifo>'`, a background thread blocks on the FIFO via
  `select` (POLL_S timeout so `_check_stall`/`_retry_dropped_answer` still get called on silence), debounces ~150ms,
  then `tmux capture-pane -p -J -t jarvis-claude` (`-J` joins wrapped lines, including the wrapped sign-in URL). On EOF
  (writer closed) the FIFO is closed and reopened so a future writer is seen again.
- `stop()`: `tmux kill-session -t jarvis-claude`, then removes the FIFO file.
- `clClaudeBridge.py`'s `_ensure_pty_started` and `run_setup` both now pick the backend via a shared `_backend_class()`
  keyed on `CURRENT_OS` — the old "Linux/tmux backend not implemented yet" guard is gone.
- Tests: `tests/test_clClaudeTmuxBridge.py`, all subprocess calls mocked (no real tmux/claude needed to run the suite).

**Bug found and fixed in the same pass:** `run()`'s credentials-parking check (`if CURRENT_OS == "Windows" and not
os.path.exists(creds_path): park forever`) was Windows-only, so on Linux the service skipped parking even with no
saved login and fell into the live MQTT message loop — which, against the mocked client in
`TestMissingCredentials::test_missing_credentials_file_blocks_instead_of_connecting`, spun a real infinite `async for`
loop (confirmed: a stray test process reached 4GB RSS at 100% CPU before being killed). Fixed by dropping the
`CURRENT_OS == "Windows"` condition — parking now applies on both OSes, matching `--setup` now being needed on Linux
too.

### 1c. Linux verification checklist — PARTIALLY DONE (2026-09-24, updated same day once `claude` was installed)
Round 1, against a real tmux server with a throwaway shell script standing in for `claude` (backend mechanics that
don't care what the spawned program actually is):
- Real `start()` spawns the session; `tmux has-session`/`capture-pane` see it; `on_screen_update` fires with real
  captured text (not a mock).
- `write()` really delivers text through `tmux send-keys -l` + `Enter` — round-tripped through a fake CLI that echoed
  stdin back, confirmed via `capture-pane`.
- **Crash resilience confirmed for real**: starting a second `ClaudeTmuxBridge` instance against an already-running
  session reuses the exact same tmux pane (same pane PID, no respawn) — `tmux pipe-pane -O` re-pointing to a new FIFO
  on reattach was also confirmed experimentally to actually redirect the pipe rather than no-op or toggle it off.
- `stop()` really kills the tmux session and cleans up the FIFO file.
- **Bug found and fixed**: `stop()` deleting the FIFO races the reader thread's EOF-triggered reopen (a session dying
  closes the FIFO's write end right as `stop()` removes the file) — this crashed the reader thread with an unhandled
  `FileNotFoundError`. This is a *likely* real scenario, not just a smoke-test artifact: it's exactly what happens on
  every stall-recovery restart. Fixed in `_read_loop()` (both the initial open and the reopen-after-EOF now catch
  `OSError` and exit the loop quietly instead of raising); regression tests added in
  `TestReadLoopFifoRace` (`tests/test_clClaudeTmuxBridge.py`).

Round 2, once the real `claude` CLI (v2.1.281, npm-installed) was available: spawned it for real via
`ClaudeTmuxBridge` against a fresh, isolated temp cwd (never touched the repo's own `data/claude_bridge_config`),
and watched `capture-pane` output live rather than mocking anything.
- **Confirmed working**: the theme picker ("Choose the text style...", `2. Dark mode ✔` pre-highlighted) and the
  login-method screen ("Select login method:", `1. Claude account with subscription` pre-highlighted) were both
  correctly auto-answered by the shared `ENTER` token, and the CLI proceeded on its own into
  "· Opening browser to sign in…" without stalling or crashing the bridge.
- **New finding**: the "Is this a project you created or one you trust?" trust menu never appeared in this run —
  the CLI went straight from the welcome banner to the theme picker. Unclear yet whether that's because the AUTO_ANSWERS
  `DOWN ENTER` case simply never got exercised here (a fresh empty temp dir may not trigger a trust prompt the way a
  real project directory does), or this CLI version's flow differs from what was true when `AUTO_ANSWERS` was written
  against an older version on Windows. Needs re-checking against a real populated repo directory; the `AUTO_ANSWERS`
  down-arrow logic itself is still unverified against the real CLI as a result.
- **Open question, not yet a confirmed bug**: `_maybe_open_signin_url`'s marker is the literal string `"Browser
  didn't open"` (the CLI's own fallback-to-manual-URL phrasing), but what actually showed on screen through the whole
  observed window was `"· Opening browser to sign in…"` / `"✽ Opening browser to sign in…"` (an animated spinner
  state) — the run was stopped (cleanly, deliberately) before seeing whether it ever falls through to the "didn't
  open" phrasing on a display-less machine, since letting it hang there longer risks it actually reaching a real
  OAuth callback with no way to complete it. Needs a longer-running check to see what the real fallback text is in a
  sandboxed/headless environment, and whether the marker string still matches current CLI wording.
- No stray processes or tmux sessions were left behind by either round (verified via `ps aux` / `tmux ls` after).

Round 3, a real `--setup` attempt against the repo's actual `data/claude_bridge_config` (not a temp dir): got much
further than round 2 before the `webbrowser.open()` call fired — theme picker and login-method screens auto-answered,
then a **real "Paste code here" screen appeared** (the CLI's OAuth flow redirected to a hosted `platform.claude.com`
page that displays a code to copy back, rather than a `localhost:PORT` loopback callback this time — apparently the
CLI can use either flow, not always the same one). This is exactly the path `run_setup()`'s `input()` handling exists
for. But the attempt was launched via an agent tool call (no real interactive terminal attached), so the moment it
tried to `input()` for the pasted code it crashed with an unhandled `EOFError` — and its own `finally: bridge.stop()`
then killed the tmux session, silently invalidating the browser tab that had just been opened for the human to use.
**Root lesson: `--setup` cannot be driven by an agent's tool calls at all** — the paste-code step needs a live human
keyboard, which no piped/tool-invoked subprocess has. It must be run by a human directly in their own real terminal.
Fixed the crash itself regardless (`run_setup()` now catches `EOFError` around the `input()` call and fails with a
clear message instead of a traceback — regression test `test_no_interactive_stdin_for_the_paste_code_step_fails_cleanly`
in `tests/test_clClaudeBridge.py`), but the underlying interactive step is fundamentally a human-only action.
Also worth noting for future live testing of this bridge: `webbrowser.open()` reaches the *real* desktop/browser even
when triggered from a sandboxed agent tool call (this environment shares the display/session) — so any test that
gets as far as opening a sign-in URL leaves a real, clickable browser tab behind, which goes stale and produces a
confusing "unable to connect" error the moment the backing process is torn down. Warn before running a test that
could reach that point, same spirit as the existing "warn before any audible test" rule below.

Still open, and needs a human to run `--setup` themselves (from their own terminal, not through an agent) to verify:
- Finish sign-in end-to-end: click Authorize, paste the code if shown, confirm `.credentials.json` gets written, confirm
  it reaches the idle chat prompt afterward.
- Fresh start against the real CLI *with a populated project directory* (to properly exercise the trust-menu path,
  which hasn't appeared in any run so far — unclear if that's specific to empty temp dirs or this CLI version):
  trust menu answered, `--continue` omitted first run and used afterwards.
- Voice question -> answer spoken once. An answer ending in "?" reopens the mic (`request_reply` path is untested live).
- Stall recovery against the real CLI: kill the child claude process, watch restart and the single re-send.

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

## Phase 4: dashboard widget (`ClaudeWidget`) — core done (2026-09-24), key-shortcut chips still open

Implemented: `src/ui/clClaudeWidget.py` (a `LogViewer`-styled read-only `ZoomTextEdit` mirror + a `QLineEdit`/Send row,
modeled on `clLogWidget.py`'s viewer and `clTodoWidget.py`'s input+`ActionRouter` pattern), wired into `clUI.py` per
the original plan exactly: a `claude_screen_signal` on `MqttThread` subscribed to `jarvis/claude/screen`, a
`WidgetTogglePill` (`claude.svg`, a plain chat-bubble icon) with `_toggle_claude`/widget id `widget_claude`, the
show/hide lists in `set_fullscreen`/`set_overlay`, and the restore branch in `load_ui_state`. Verified live by running
`clUI.py` against a real Xvfb display and driving it with `xdotool`: the pill appears in the dock, opens the widget
with correct chrome, a real `jarvis/claude/screen` MQTT message renders in the terminal view, and typing + Send
correctly dispatches `claude.ask` with the right payload.

**This closed a real gap**: `ClaudeBridgeService.run()` used to park forever whenever `data/claude_bridge_config/
.credentials.json` didn't exist yet (`git blame`-visible reasoning: no UI existed to interact with the onboarding
screens, so starting a session nobody could see was pointless). Now that the widget exists, that reasoning no longer
holds — `run()` starts the session anyway when signed out, `force_fresh=True` only on that first start (so it can't
silently reattach to a stale session an earlier aborted attempt left on an expired OAuth prompt), and the resulting
"paste code" screen is both visible and answerable through the widget: paste the code into its input, it dispatches
`claude.ask` to `jarvis/claude/question` same as any other message, and the running bridge relays it into the
terminal with `write()`. `run_setup()`/`--setup` still exists as a terminal-only alternative (also `force_fresh`-
guarded now), but the widget is the primary path going forward.

Still open from the original design notes:
- Buttons/keys for Esc (interrupt), Shift+Tab, arrows -- publishing `{"keys": ...}` (the mockups sketched these as
  small chips under the input; not yet wired to real clicks).
- Permission prompts must be visible and answerable from the widget (today the session runs in auto mode, so this
  hasn't been exercised).
- Scrollback: the 120x40 grid only shows the current screen. Publish scrollback (tmux `capture-pane -S -`) or keep a
  history in the widget.
- Widget size is owned by `ui_state.json` (see the `widget-size-source-of-truth` memory); never `adjustSize()` the
  content -- `ClaudeWidget.get_standalone_min_size()` only sets the first-ever default.
- Slash-command IntelliSense: `QCompleter` on the widget's `QLineEdit`, populated from a hardcoded `SLASH_COMMANDS`
  list (a reasonable starting set from general knowledge, not verified against the live CLI's exact current list).
  Since every entry starts with `/`, the popup naturally stays hidden for ordinary questions and appears the moment
  `/` is typed -- no extra trigger logic needed.
- Icon: `assets/icons/claude.svg` is a small hand-drawn robot-head glyph (antenna, two eyes, mouth grille via
  `fill-rule="evenodd"` holes), not the official Anthropic mark -- swap in the real asset if/when available.

### "/terminal" ⇄ "/claude": a second backend, same widget (2026-09-24)

`ClaudeBridgeService` now runs two independent backends side by side -- `self.bridge` (the claude session) and
`self.terminal_bridge` (`src/utils/clPlainTerminalBridge.py`, a much simpler tmux-backed plain shell: no onboarding
auto-answers, sign-in URL detection, or stall recovery, none of which apply to a bare shell prompt) -- and a
`self.mode` flag deciding which one's screen actually gets published to `jarvis/claude/screen` and which one receives
typed input from `jarvis/claude/question`. Typing `/terminal` or `/claude` into the widget doesn't get forwarded as
literal text into either session; `run()`'s message loop intercepts both as local mode switches. Neither backend is
ever stopped on a switch -- both keep running in the background, so toggling back is instant and never loses shell
or conversation state; `refresh_screen()` (added to `ClaudeTmuxBridge`, `ClaudePtyBridge`, and
`PlainTerminalBridge`) forces an on-demand capture on switch so the widget shows the right content immediately
instead of waiting for the next output change. `_last_claude_screen`/`_last_terminal_screen` cache each backend's
latest screen even while inactive, so switching back to a backend that's had no new output still republishes
something accurate rather than nothing.

Linux-only for now, same story as the claude backend's own history: `_terminal_backend_class()` raises
`NotImplementedError` on Windows (a ConPTY-backed `cmd.exe`/`powershell.exe` equivalent would be the follow-up), and
`run()`'s `/terminal` handler catches that and reverts to claude mode rather than leaving the service stuck in a mode
with no working backend.

Verified: `PlainTerminalBridge` smoke-tested live against a real tmux server (`write()` round-tripped through a real
shell's `echo`, `refresh_screen()` triggers a real on-demand capture, `stop()` really kills the session) -- same
verification style as `ClaudeTmuxBridge`'s own Phase 1c testing. Full mode-switching logic (both slash commands,
routing, screen-update suppression by mode, the Windows-fallback path) covered by
`tests/test_clClaudeBridge.py::TestTerminalModeSwitching`.

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
- Warn before any test that could open a real browser tab (`webbrowser.open()`) — it reaches the real desktop even from
  a sandboxed agent tool call, so a torn-down test process leaves a stale, confusing tab behind. Never run `--setup`
  itself via an agent tool call at all: the paste-code step needs a real human terminal (see Phase 1c, Round 3).
- When the ecosystem is live, end each turn with a one-sentence spoken summary via `jarvis/sys/speak` (except inside the
  bridged session itself).
- Windows-only gotchas: each service is two python processes (launcher stub plus child); kill by script name with
  `taskkill /PID <stub> /T /F`, and confirm which process you're killing (killing the supervisor's stub takes everything down).

## Open decisions

- Should bridge speech ignore silent mode? Currently it respects it.
- Trust prompts and permission mode for the dev-session role (auto mode vs. approving in the widget).
- Whether voice questions and the dev session share one session or get separate ones.
