# Notification feed widget (WhatsApp / Discord / Instagram / anything) — small start plan

Status: **plan only.** Written 2026-11-18. Replaces the idea of "a social media widget with real chat clients".

## Why a feed and not clients

- Instagram and WhatsApp have no public API for personal messages (the working routes are unofficial and can get the account banned); reading your own Discord account with a script breaks Discord's terms (a bot only sees servers it was added to).
- Message text would travel over MQTT, where the debug sniffer and the Live Logs widget print payloads, in a public repo.
- The operating system already receives every message as a notification. Reading **those** needs no login, covers every app at once, and can be limited to sender + a short preview. Not possible: replying from the widget.

## Privacy rules (decided before any code)

- Show app, sender/title and a **preview cut to ~80 characters**; never store the full body.
- Not persisted to disk by default (memory only, last N=30, cleared on restart); a setting can turn the feed off.
- The MQTT topic is **excluded from the debug sniffer and from the Live Logs view** (add it to `NOISY_TOPICS` in `src/utils/clLogTail.py` and to the supervisor's sniff exclusions) so previews never reach `logs/latest_run.log`.
- An app allow-list in `core.json` (`settings.notification_feed.apps`, empty = nothing shown) so the user chooses WhatsApp, Discord, Instagram... explicitly.

## The unknown that decides everything: how to read notifications on Windows

| Route | Verdict so far |
|---|---|
| WinRT `UserNotificationListener` (`winsdk` is already in `requirements.txt`) | Official, but documented for apps **with package identity**; a plain `python.exe` may be refused or get "access denied" even after the consent prompt. **Unverified: test first.** |
| Read `%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db` (SQLite, XML payloads) | Works without identity on current Windows 10/11 builds as far as known, but undocumented and can change with an update; copy the file before reading (it is locked/WAL). **Unverified.** |
| Linux | `dbus-fast` (already a Linux requirement): become a monitor of `org.freedesktop.Notifications` and read `Notify` calls. Standard, no consent. |

## Step 0 — spike (about 1 hour, two small throwaway scripts)

1. Windows: try `UserNotificationListener.current.request_access_async()` from the venv python and `get_notifications_async(NotificationKinds.TOAST)`; send a test message to yourself in WhatsApp/Discord and see whether it appears. If refused, try the `wpndatabase.db` route and parse one toast's XML (app id, title line, body line).
2. Linux (laptop): a `dbus-fast` monitor printing app name, summary and body of each `Notify`.
3. Record which apps' notifications arrive with usable sender text (WhatsApp Desktop, Discord, the Instagram web push through the browser all differ).
4. Decide the Windows route from facts; if neither works, stop here and say so.

## Step 1 — service

New `src/clNotifications.py` (independent MQTT service, like the others; supervisor spawns it): per-OS reader behind one small interface (`async def stream() -> {app, title, text, time}`), filtered by the allow-list, preview-trimmed, publishing `jarvis/sys/notification` (not retained) and a retained `jarvis/sys/notification_feed` with the last N. OS branches on `CURRENT_OS`. Tests: parsing of a sample toast XML and a sample D-Bus `Notify`, allow-list, trimming, a disabled feed publishes nothing, the topic never reaches the log view.

## Step 2 — widget

`src/ui/clNotificationWidget.py`, a small dashboard widget (list of rows: app icon/letter, sender, preview, time; click clears one; a "clear all"; unread dot). Spawned like the other widgets (`spawn_widget`, saved geometry, per-monitor layouts). A new message also pops the existing notification toast area if one exists. Tests: rows, trimming, cap at N, off by default until the allow-list has an app.

## Step 3 — optional

Voice: "any messages?" reads the last previews (through the existing speak path, respecting the same allow-list); a quiet-hours setting. Reply stays out of scope.

## Docs

`docs/context/` gets a short `notifications.md` and a row in the `CLAUDE.md` topic table when it is built.
