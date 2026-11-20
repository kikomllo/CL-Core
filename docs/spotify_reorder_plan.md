# Spotify: reorder tracks from the Up Next drawer — plan

Status: **plan only, nothing implemented.** Written 2026-11-18, after the clickable Up Next rows (`play_queue_item`).

## What the Spotify API allows (checked against the docs)

| Wanted | Web API | Verdict |
|---|---|---|
| Reorder the **playback queue** (what Up Next shows, what the app lets you drag) | None. The queue has only `GET /me/player/queue` and `POST /me/player/queue` (append). No move, no remove. The app's drag-and-drop uses a private protocol. | **Impossible** through the Web API |
| Reorder a **playlist** | `PUT /v1/playlists/{id}/items`, body `{range_start, insert_before, range_length, snapshot_id}`, returns the new `snapshot_id` | **Possible**, with caveats below |

So "change the order" can only mean *move the track inside the playlist that is playing*, and the queue follows from that. It cannot reorder user-added "Add to queue" entries, and it does not work on albums, Liked Songs, radio or a shuffled playlist.

Caveats that shape the design:

1. **Scopes.** Reorder needs `playlist-modify-public` and `playlist-modify-private`. `clSpotify.py` asks only for the read scopes, so a one-time re-consent is required (see Auth).
2. **Only playlists the user owns or that are collaborative** can be modified (otherwise 403).
3. **February 2026 migration.** Dev-mode apps lost `/playlists/{id}/tracks`; the path is now `/items`, and in the response rows `track` became `item` (`track` is now a boolean). spotipy 2.26.0 (installed) already calls `/items` in `playlist_reorder_items`, but anything we read through `playlist_items` must use `item`, not `track`.
4. **Shuffle.** With shuffle on, Up Next is a random order, so a playlist reorder would not change what plays next. Reorder is disabled while shuffle is on.
5. **User-queued tracks** come first in Up Next and are indistinguishable from context tracks in the API response. A row is only movable if it maps onto the playlist (see Mapping).
6. **Duplicates.** A playlist can contain the same track twice; positions, not URIs, decide.
7. **Unknown: does the running Spotify client pick up a changed playlist mid-playback?** Not documented. This must be tested before building the UI (Step 0).

## Step 0 — spike (30 min, decides the rest)

A throwaway script against a *test playlist the user owns*, playing, shuffle off:

1. Move track N+3 to N+1 with `sp.playlist_reorder_items`.
2. Watch `sp.queue()` and the Spotify app for ~10 s: did Up Next change, and did the *next-track button* follow the new order?
3. If it did not: try the fallback `sp.start_playback(context_uri=..., offset={"uri": <current uri>}, position_ms=<current progress>)` and measure the audible gap and whether the queue is now correct.
4. Confirm the 403 on a playlist the user does not own, and the exact error text.

Outcomes: (a) client follows → no playback action needed; (b) only the fallback works → a short hiccup per drop, so batch it (see UI: apply on drop, not live); (c) neither → stop, the feature would only edit the stored playlist and the user would not hear the change. Decide with the user before going further.

## Auth (one-time)

- Add the two modify scopes to `self.scope` in `clSpotify.py`.
- **Danger:** `SpotifyOAuth` with a cached token whose scopes differ starts an interactive login (browser or pasted URL) *inside the service*, which would hang a background process. So: add a `--auth` mode (`python src/clSpotify.py --auth`) that deletes/ignores the stale `.cache` and runs the consent flow in a foreground terminal once; the service itself must not start the flow when the cached token lacks a scope (check `get_cached_token()` scope first and degrade to "reorder unavailable, run --auth").
- Both machines (desktop and laptop) each need their own consent, since `.cache` is per checkout.
- The 5-user dev-mode limit is unaffected.

## Backend (`src/clSpotify.py`, `src/utils/clQueueTracker.py`)

New action `move_queue_item` on `spotify.control` (schema in `config/actions.json` gets `from_index`, `to_index` next to `uri`/`queue_index`).

`SpotifyManager.move_queue_item(uri, from_index, to_index)`:

1. **Guards** (each returns a clear message the UI shows; no API call is wasted): scope present; context type is `playlist`; shuffle off (local status already has it); playlist owned by the current user or `collaborative` (`playlist(id, fields="owner.id,collaborative,snapshot_id")`, user id cached once from `current_user()`).
2. **Playlist order cache**: `PlaylistOrder` (new, in `clQueueTracker.py` or a sibling) holds the ordered URI list of the playing playlist plus its `snapshot_id`. Loaded with `playlist_items(id, fields="items(item(uri)),next,total", limit=100)` paging; re-validated by one cheap `snapshot_id` request; after our own reorder it applies the move locally and takes the snapshot id from the response, so consecutive drags cost one request each.
3. **Mapping** from Up Next rows to playlist positions: `cur` = position of the current track (first occurrence at/after the last known position, which handles duplicates); row `i` should sit at `cur + 1 + i`. **Verify** `playlist[cur+1+i].uri == queue[i].uri` for the dragged row and its target; if not (user-queued tracks, drift), refuse with "Queue has tracks added outside the playlist; can't reorder" rather than moving the wrong thing.
4. **Reorder**: `range_start = cur+1+from`, `range_length = 1`, `insert_before = cur+1+to` (+1 when moving down, as the API inserts *before* the index), `snapshot_id` = cached (a 409/changed snapshot → reload once and retry once).
5. **Make the player follow** per Step 0 (nothing, or the `start_playback` fallback with the current uri and progress; use the local-first path for position so no extra API call).
6. **Refresh the view**: move the item in `QueueTracker` immediately, publish the retained `jarvis/sys/spotify_queue`, then `refresh_queue()` to confirm (same shape as `play_queue_item`).

Cost: ≤ 3 API requests on the first drag of a playlist (owner/snapshot, paged items, reorder), 1 afterwards; nothing while nobody drags (matches the "no one watching, no API cost" rule of the queue tracker). All API-side, so identical on Windows and Linux.

## UI (`src/ui/clMediaWidget.py`)

`QueueRow` is currently a click-to-play button. Add, in order of cost:

1. **Move up / move down** small arrow buttons on row hover (cheap, accessible, no click-vs-drag ambiguity). Hidden when the guards say reorder is unavailable (the widget gets `reorderable` from a new field in the retained queue payload: `{"queue": [...], "remaining": n, "reorderable": bool, "reason": "..."}`), so a shuffled or non-playlist session shows no controls instead of failing on click.
2. **Drag and drop** (second iteration): press + move beyond a threshold (`QApplication.startDragDistance()`) starts a drag and suppresses the click; a thin insertion line shows the target; the move is sent **on drop** (one request, not live) with the optimistic order shown and the rows dimmed ("moving...") until the retained queue message returns, same pattern and 4 s timeout as `_set_queue_refreshing`.

Rows without a `uri` stay non-movable, as with click-to-play. Drawer state persistence and the minimum-width measurement are unaffected (same rows).

## Tests (each new branch gets one, per the repo rule)

- `tests/test_clSpotify.py::TestMoveQueueItem`: refuses on shuffle / non-playlist / not-owned / missing scope; correct `range_start`/`insert_before` for moving up and down; duplicate tracks; mismatch (user-queued) refuses without calling the API; snapshot conflict retries once; cached order updated locally after a move (second move makes no fetch).
- `tests/test_clQueueTracker.py`: `move` on the local queue is idempotent and keeps `remaining`.
- `tests/test_clMediaWidget.py`: hover arrows dispatch `move_queue_item` with the right indices; hidden when `reorderable` is false; the optimistic order and its timeout; (iteration 2) a drag does not fire the click-to-play.
- Auth: the service with a cached token missing the scope does not start an interactive flow.

## Docs

`docs/context/cross-platform.md` (the Spotify queue section: new action, the scope, why the queue itself cannot be reordered), `docs/context/ui.md` (row controls), and the `--auth` step wherever Spotify setup is described.

## Order of work

0. Step 0 spike (decides everything). 1. Auth scope + `--auth`. 2. Backend + tests. 3. Hover arrows + tests. 4. Drag and drop. 5. Docs, run `tests/test_clSpotify.py`, `tests/test_clQueueTracker.py`, `tests/test_clMediaWidget.py`, restart the UI and `clSpotify`.

## Open questions for the user

- Is moving a track in the real playlist acceptable (it is permanent and visible in the Spotify app on every device), as opposed to a temporary reorder? The API only offers the permanent one.
- Which playlists will this be used on: only your own, or also collaborative ones?
- Hover arrows first, or go straight to drag and drop?

Sources: [Update Playlist Items](https://developer.spotify.com/documentation/web-api/reference/reorder-or-replace-playlists-items), [Get the User's Queue](https://developer.spotify.com/documentation/web-api/reference/get-queue), [February 2026 migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide).
