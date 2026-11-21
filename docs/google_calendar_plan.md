# Google Calendar integration — draft plan

Status: **draft, nothing built; the four open decisions were answered on 2026-11-21 (see "Decisions made" at the end).** Groundwork already in the repo is listed in `docs/context/calendar.md` ("Google Calendar integration"): the Google-shaped event model, `source`/read-only handling, the hollow-dot/GOOGLE-tag presentation, and the hidden sync label (`CalendarWidget.set_sync_status`).

## Goals

1. Google events show in the drawer calendar, the day sheet, the voice briefing and "what's on my calendar" — with no UI rewrite.
2. Read-only first (phase 1) to prove the auth and sync; create/edit/delete of Google events right after (phase 2), because the real goal needs it: events created on one device must appear on every other one.
3. **The same calendar on several devices**, with Google as the hub (see "Multiple devices").
4. Same rules as the rest of CL-Core: its own process, MQTT only, no secrets in the repo, works on Windows and Linux, light on CPU/RAM, never touches the network from tests.

Non-goals: webhooks/push notifications (need a public URL), multiple Google accounts, a full recurrence editor. Other providers: not now; a **CL-Core account** option is a possible later step, so keep `source` and the single merge point provider-neutral (nothing Google-specific may leak into `clCalendarEvents` or the UI beyond what `source: "google"` already carries).

## Multiple devices

There is no CL-Core server and no CL-Core account. Google *is* the sync hub:

- Every machine runs its own `clGoogleCalendar.py`, signs in itself (one Connect click per machine) and keeps its own cache, so each works offline.
- The same OAuth client works everywhere: copy `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` into each machine's `.env` (gitignored, so it travels by hand, never through git). Tokens are per machine (`data/gcal/token.json`) and are not shared.
- A change made on one device shows on the others at their next poll (`sync_interval_s`, default 5 min; `sync_now` or a wake from sleep refreshes sooner). No push without a public URL, so a few minutes of delay is expected.
- **Local events stay on their machine.** That is why new events default to Google once write-back exists (decision 2); the local calendar remains for offline/private events, and an optional "push my local events to Google" is a phase-3 extra.

## Architecture

```
Google API <--httpx--> clGoogleCalendar.py ──writes──> data/gcal/events.json (cache)
                              │                               │
                    jarvis/sys/gcal/status (retained)   jarvis/sys/gcal/updated
                              │                               │
                          clUI (label,                  clUtilities merges local + cache
                          Settings tab)                 -> jarvis/sys/calendar/status
```

- **New service `src/clGoogleCalendar.py`** (native, registered in `clJarvis.NATIVE_SERVICES` / `config/modules.json` as "Google Calendar"; publishes `module_ready`). Owns OAuth tokens and every Google request. Never imports or is imported by another service.
- **One merge point.** `clUtilities` already owns `jarvis/sys/calendar/status`. Refactor its three readers (`handle_calendar_list`, `handle_calendar_briefing`, and the delete path) onto one `load_events()` that returns local files + the Google cache, so the UI, the briefing and voice reads all see the same list. The UI keeps calling `calendar.read`; the only UI change in phase 1 is driving `set_sync_status`.
- **Why a cache file and not a retained topic:** the briefing runs at startup and must work offline; a file survives restarts and broker restarts, and the UI never has to merge anything. The service writes it atomically (temp file + replace), then publishes `jarvis/sys/gcal/updated`; Utilities subscribes and re-publishes `calendar/status`.
- **HTTP: `httpx` + a small OAuth helper, not `google-api-python-client`.** `httpx` is already a pinned dependency; the Google client stack adds ~10 packages and tens of MB of imports for four REST calls. (If that proves too fiddly, `google-auth` + `google-auth-oauthlib` alone is the fallback — still not the discovery client.)

### Topics

| Topic | Direction | Payload |
|---|---|---|
| `jarvis/sys/gcal/control` | in | `{"action": "connect" \| "disconnect" \| "sync_now" \| "set_calendars", ...}` |
| `jarvis/sys/gcal/status` | out, **retained** | `{"state": "disconnected" \| "connecting" \| "syncing" \| "synced" \| "offline" \| "reauth" \| "error", "account", "last_sync", "next_sync", "calendars": [{"id","summary","enabled","access"}], "auth_url", "error"}` |
| `jarvis/sys/gcal/updated` | out | `{"count", "ts"}` — nudge for Utilities |
| `jarvis/sys/calendar/create` / `control` | in (phase 2) | existing topics; the service acts only on events whose `source` is `google` |

Add the three `gcal` actions to `config/actions.json` so the UI uses `ActionRouter` (no hand-rolled `publish.single` like the event dialog does today).

## Auth

- OAuth 2.0 **installed-app / loopback flow with PKCE**. The service binds `127.0.0.1` on a free port, opens the system browser (`webbrowser.open`) at the consent URL, receives the code, exchanges it, stores the tokens.
- **Headless/remote Linux:** if no browser can be opened, publish the URL in `gcal/status.auth_url` so the Settings tab can show/copy it (same idea as the Claude sign-in code UI).
- **Scopes:** phase 1 `calendar.readonly` (minimal, read-only consent); phase 2 re-consents for `calendar.events`.
- **Secrets:** `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in `.env` (loaded through `EnvLoader`, like `SPOTIPY_*`); tokens in `data/gcal/token.json` (add `gcal` to the `data/` entries in `.gitignore`; write with owner-only permissions where the OS supports it). Nothing goes in `core.json`.
- Access tokens are refreshed in memory a minute before expiry. A `invalid_grant` on refresh means the grant was revoked/expired → delete the token, state `reauth`, UI shows "Reconnect".
- Setup docs (in this file's phase-1 PR): create a Google Cloud project, enable the Calendar API, create a **Desktop app** OAuth client, paste id/secret into `.env`. Because this is a public portfolio repo, the repo ships *no* client id.

## Sync

- **Polling, windowed refetch.** Every `sync_interval_s` (default 300), plus on `sync_now`, plus once at start and on wake from sleep (a long gap between ticks):
  `events.list` per enabled calendar with `timeMin = today − past_days`, `timeMax = today + future_days`, `singleEvents=true` (Google expands recurrences — we never parse RRULEs), `orderBy=startTime`, `maxResults=250`, following `nextPageToken`.
- Send the previous response's **ETag** as `If-None-Match`; an unchanged calendar answers `304` with no body, so a quiet sync is one tiny request per calendar. (`syncToken` is not used: it can't be combined with `timeMin/timeMax/orderBy`.)
- Skip `status: "cancelled"`. Replace the whole cache for the window each time — deletions on Google's side then disappear without tombstone handling.
- **Normalise to the existing model** so `clCalendarEvents` and the UI need nothing new:
  - `id`: `g_<calendarIndex>_<googleId>` (stable, never collides with local numeric ids); the raw ids go in `gcal_id` / `calendar_id`.
  - `summary` (default "(no title)"), `start`/`end` passed through untouched (`dateTime` with offset, or `date`), `description`, `location`, `htmlLink`, `colorId`, `recurringEventId`.
  - `source: "google"`; `readOnly: true` unless the calendar's `accessRole` is `owner`/`writer` **and** phase 2 is on.
  - Note: all-day `end.date` is **exclusive** in Google's model; check `event_end` consumers (none today besides display) before showing ranges.
- **Failure behaviour:** no network → state `offline`, keep serving the cache, exponential backoff (cap 15 min). `401` → refresh once, then `reauth`. `403 rateLimitExceeded` / `5xx` → backoff. Never crash the process; log with `clLogging` like other services.
- **Cost:** one idle asyncio task, no threads; imports only `httpx` + stdlib; no timers while disconnected.

## UI changes

- **Phase 1**
  - Sync label (`set_sync_status`): "Google · 2 min", "Google · offline", "Reconnect Google" — driven by the retained `gcal/status`; hidden when disconnected/never configured.
  - **Settings → new "Calendar" tab** (next to Presence): Connect / Disconnect, account email, last sync + "Sync now", past/future window, interval (phase 1 syncs the **primary calendar only**; choosing calendars is a button on the calendar itself, phase 3). Follows the existing tab pattern (`_create_scroll_tab`, settings written through `_update_core_json("calendar_settings", …)` — it saves live, no module reboot needed because the service watches `core.json`, or a "Save & Reboot Calendar" if watching proves awkward).
  - Nothing else: the hollow dot, GOOGLE tag and read-only behaviour already exist.
- **Phase 2**
  - The `+` dialog gains a destination; **default is Google's primary calendar** (decision 2), from `calendar_settings.default_new_event_target`. Voice "add to calendar" uses the same default. With only the primary calendar connected it is a Local / Google choice; the full picker comes with phase 3.
  - `is_editable` becomes `source == "local" or (source == "google" and not readOnly)`; delete/edit dialogs route by source.
  - **Fix the edit path first:** today an edit re-sends only title + time to `handle_calendar_create`, which rewrites the whole file and silently drops `description`/`end`. Edits must merge into the existing event, or Google events would lose data.
  - Optimistic update in the cache, then the real request, then a resync; a failure restores the event and publishes `gcal/status.error`, which the UI shows briefly.
  - Recurring events: edit/delete only "this event" (the expanded instance) in v1; a "this and following" choice is later.

## Config

```jsonc
// core.json -> settings.calendar_settings (every key optional)
{
  "google": {
    "enabled": false,
    "sync_interval_s": 300,
    "past_days": 30,
    "future_days": 365,
    "calendars": ["primary"],             // the calendars shown; phase 3's button edits this list
    "default_new_event_target": "google:primary"   // "local" | "google:<calendar id>"; "local" until write-back exists
  }
}
```

## Phases

| Phase | Delivers | Done when |
|---|---|---|
| **1. Read-only sync** | service + auth + cache + merge refactor + sync label + Settings tab + tests + setup doc | A Google account connects from Settings; its events appear in the drawer, the day sheet, and the morning briefing; disconnecting removes them; offline start still shows the cached ones. |
| **2. Write-back** | destination picker, edit-merge fix, create/edit/delete via the API, `calendar.events` scope upgrade | Create/edit/delete a Google event from the dashboard and see it in Google; failures roll back visibly. |
| **3. Several calendars** | A **button on the calendar** (header, next to Today) opens a small list of the account's calendars (name, colour dot, checkbox) to choose which are shown; each calendar's events get its own colour (Google's `colorId`/calendar colour) so they can be told apart; the `+` dialog gets a **calendar switch** (a dropdown of the writable calendars, remembering the last choice) to pick where a new event goes. Writes the `calendars` list in `core.json`; the service starts syncing the newly enabled ones at once. | Tick a second calendar and its events appear, coloured; create an event in it from the dialog and see it in Google. |
| **4. Extras** | recurring "this and following", all-day creation, optional push of local events to Google | As requested. |

## Tests (all offline)

- `tests/test_clGoogleCalendar.py` with `httpx.MockTransport`: normalisation (timed, all-day with exclusive end, no title, cancelled skipped), windowed query params, pagination, ETag → 304 keeps the cache, token refresh, `invalid_grant` → `reauth`, offline → `offline` + backoff, atomic cache write, status payload shape, `connect` with a fake browser and loopback request.
- `tests/test_clUtilities.py`: merged list sorts local + Google together; a corrupt cache file is ignored; briefing includes Google events and says "all day" for date-only ones.
- UI: sync-label states, Settings tab writes `calendar_settings` and dispatches `gcal/control` (router mocked), read-only/editable rules per source.
- Never read or write the real `data/gcal/` or `.env` in tests; patch paths to `tmp_path` (like `isolated_state` in `test_clUI.py`).

## Risks and open questions

- **Google "Testing" consent screens expire refresh tokens after 7 days** for External apps. Options: publish the app as "In production" (it stays *unverified*, so users see a warning screen, fine for a personal install) or use a Workspace-internal app. Needs a decision before phase 1 is demoed.
- Each user needs their own Cloud project/client id (public repo → no shared credentials). The setup doc has to be short and exact.
- Data privacy: events are cached in `data/gcal/` (gitignored); `Disconnect` should delete the token and the cache.
- Timezones: Google returns offsets; `clCalendarEvents` already converts to local wall-clock. All-day events must stay date-only (never converted through a timezone).

### Decisions made (2026-11-21)

1. **Google sign-in first.** CL-Core's own accounts are a possible later step (keep the code provider-neutral, see Non-goals). Order: read-only (phase 1) to de-risk auth and sync, then write-back (phase 2) straight after, since the multi-device goal needs it.
2. **New events go to Google by default** once write-back exists.
3. **Primary calendar only at first; several calendars later, as a button on the calendar plus a calendar switch in the create-event dialog** (phase 3), not a Settings checklist.
4. **Plain REST via `httpx`**, as drafted (recommended: it is already a dependency, and Google's client stack adds ~10 packages for four REST calls). Fallback if the OAuth helper gets fiddly: `google-auth` + `google-auth-oauthlib`, never the discovery client.

Still open before phase 1 is demoed: publish the Google app as "In production" (unverified) so refresh tokens do not expire after 7 days (see Risks).
