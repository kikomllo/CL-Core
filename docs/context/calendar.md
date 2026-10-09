# Calendar

Read when working on `src/clCalendarWidget.py`, the side drawer (`src/ui/clDashboardDrawer.py`, `src/ui/clDrawerControls.py`), the calendar handlers in `src/clUtilities.py`, or the Google Calendar integration. The look is the "U3" design from the drawer design canvas.

## Drawer layout (`DashboardDrawer`)

Top to bottom, in a 400 px drawer (350 px below 1920 px; `clUI._calendar_drawer_width` is the single rule):

1. **`CalendarWidget`**: header (month title + year, optional Google sync label, `<` `>`, Today), the DAY / WEEK / MONTH pill (`SegmentedControl`), then the grid. It takes its natural height; the sheet takes the rest (don't give the calendar a stretch factor).
2. **The sheet** (`QFrame#DaySheet`, rounded top, orange top edge), in this order: drag handle, **`DayAgenda`** (the selected day: "Fri 9 / 4 EVENTS / +", its events, then links to the next two busy days), a divider, **`WidgetCarousel`** (round icon dock + the stack of To-do / Reminders / Media / Lights widgets), and the **`MediaPill`**.
3. A faint orange glow behind the header is painted in `DashboardDrawer.paintEvent`.

`clUI.py` depends on these attributes, so keep them: `drawer.calendar` (`load_events`, `update_scaling`), `drawer.up_next` (`load_events`; it is the `DayAgenda`), `drawer.carousel` (`stack`, `todo_widget`, `reminder_widget`, `media_widget`, `lights_widget`, `update_indicator`), `drawer.media_pill` (`update_status`, fed from `_handle_media_status`), `drawer.update_scaling`.

### Calendar modes

`current_mode` is `month` (square-cell grid), `week` (one row), `day` (grid hidden, the sheet *is* the day) or `year` (month picker, opened by clicking the title; the pill shows nothing selected). `current_date` is the selected day; `day_selected_signal` fires on any selection change and drives the sheet. `<` `>` step a month / week / day / year by mode (month steps clamp the day). The sync label (`set_sync_status`) is hidden until the Google integration drives it.

### Sizing rules (each cost a bug once)

- **Day cells are squares.** The calendar sets its own height from its width (`fitted_height`/`_fit`, from `resizeEvent` and `update_ui`) and pins every cell to the cell width. Height depends only on width, so there is no resize feedback loop. Month shows exactly its week rows (4-6); week is one row; the year picker is as tall as a 5-row month so the panel doesn't jump.
- **The UI scaler wraps `setFixedSize`/`setFixedHeight`/`setFixedWidth`/`resize`** and multiplies their arguments by the screen scale. Pass them *unscaled* values, or pin an already-scaled size with `setMinimumHeight`+`setMaximumHeight` (`_exact_height`), which are not wrapped. In tests/harnesses use `resizeUnscaled` for drawer-sized windows.
- **`ElidedLabel`** (single-line, ellipsis) asks for the full text width plus one average character (what `QLabel` itself asks for) but has a minimum width of 0, so a long title can't widen the drawer. Long calendar titles (a week range, "Wednesday") switch to the smaller `DrawerMonthTitleSmall` for the same reason.
- Anything small and round is hand-painted (`RoundButton`, `EventDot`, `DayBars`, the media cover); see `docs/context/ui.md`. `RoundButton` overrides the global `QPushButton` stylesheet, otherwise the global square border shows under the circle.

### Orange budget ("U3")

Orange marks *surfaces*: cell/sheet/pill borders, the sheet's tint, hairlines, the glow, and state (selected day, active pill segment/dock icon, badge, `+`, play). Text and event marks are warm cream (`#ffe6cc`, `#fff4e6`, dim `#a89a8c`); local events are solid dots/bars and Google ones hollow, plus a GOOGLE tag. There is no teal anywhere.

## Event model

- Events use Google Calendar's resource shape: `id`, `summary`, `start`/`end` holding `dateTime` (timed) or `date` (all-day), plus `source`. Locally created events are stored one-per-file in `data/events/` and stamped `source: "local"`.
- **Never read `ev['start']['dateTime']` directly.** Go through `src/utils/clCalendarEvents.py` (`event_start`, `is_all_day`, `time_label`, `is_upcoming`, `sort_key`, `starts_on`, `is_editable`). It converts tz-aware times to local wall-clock (don't just strip `tzinfo`), handles all-day events, and returns `None`/sorts last for unreadable events instead of raising.
- `is_editable`: only `source == "local"` (and not `readOnly`) events open the edit dialog from the sheet (there are no per-row buttons; the dialog has Delete). Google events are read-only until write-back exists.
- A new event from `+` defaults to the selected day.
- Gotcha: `strftime("%-d")` is invalid on Windows; build day numbers from `dt.day`.

## Google Calendar integration (prepared, not built)

Done so far: the event model above (all-day, tz-aware, `source`, read-only handling), the hollow-ring/GOOGLE-tag presentation, and the (hidden) sync label. Not done: OAuth, sync, any config keys.

Full draft plan (architecture, auth, sync, phases, tests, open questions): `docs/google_calendar_plan.md`.

Planned shape, following the MQTT-only rule:
- A new `src/clGoogleCalendar.py` service owns OAuth tokens and the Google API. It never touches the UI directly.
- It writes fetched events (`source: "google"`, ids prefixed e.g. `g_`) somewhere `handle_calendar_list` can merge them into `jarvis/sys/calendar/status` (a cache file under `data/` or a retained MQTT topic; undecided), so the UI needs no changes beyond calling `set_sync_status`.
- Write-back (create/edit/delete) would go through the existing `jarvis/sys/calendar/create` / `control` topics, routed by the event's `source`; flip `is_editable` once that exists.
- Settings would live under `settings.calendar_settings` in `core.json`; secrets (client id/secret, tokens) in `.env`/`data/`, never `core.json`.
