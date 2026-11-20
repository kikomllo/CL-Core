# Spotify: visual search of playlists and songs — plan

Status: **plan only, nothing implemented.** Written 2026-11-18.

Goal: from the dashboard, find one of *your* playlists or any song by typing, see covers/titles, and play it, play it inside its playlist, or add it to the queue — without voice and without opening the Spotify app.

## What exists today

- `clSpotify.py` searches only for voice: `_play_playlist` (your 50 first playlists, fuzzy, then global), `_play_track_fuzzy` (`search(limit=5)`, a confidence score, a numbered text choice list in `search_cache`). Nothing returns structured results to a UI.
- There is **no "add to queue" action** and no "play this exact uri" action (only `play_choice` for a number from the last voice search, and the new `play_queue_item`).
- The media widget has no artwork and no network image code; the UI never talks to Spotify.
- Scopes today: `user-read-playback-state user-modify-playback-state playlist-read-private playlist-read-collaborative` — enough for searching and for listing playlists.

## What the Spotify API gives us (after the February 2026 dev-mode migration)

| Need | Endpoint | Notes |
|---|---|---|
| Your playlists | `GET /me/playlists` (limit ≤ 50, paged) | name, owner, image urls, track count. One request per 50; cache it. Filtering your playlists is then **local and instant**, no request per keystroke. |
| Songs / playlists anywhere | `GET /search?type=track,playlist` | **limit max is now 10 (default 5)**, page with `offset`. `popularity`, `available_markets` are gone, so no popularity ranking; Spotify's own relevance order is what we get. |
| Tracks inside a playlist | `GET /playlists/{id}/items` | **Only returned for playlists you own or collaborate on.** For a playlist you merely follow, `items` is absent: the UI can play it as a whole but cannot list or pick its songs. Rows are `item`, not `track`. |
| Play | `PUT /me/player/play` with `uris` (one song) or `context_uri` (+ `offset` to start inside a playlist/album) | already used by `clSpotify.py` |
| Queue | `POST /me/player/queue?uri=` | spotipy `add_to_queue`; needs an active device |
| Liked Songs | saved-tracks / `/me/library` | needs `user-library-read` (re-consent) and the endpoint changed in the migration: **verify in the spike**, optional phase |
| Covers | `images[]` on playlists and on a track's `album` | pick the smallest image ≥ the thumbnail size (64–300 px); no auth needed to download |

Consequences that shape the design:

1. Two sources in one box: **My playlists** (local filter, instant) and **Spotify** (remote search, debounced).
2. Drilling into a playlist to pick a song works only for owned/collaborative ones; for the rest the row offers just Play and the UI says why ("Spotify doesn't share this playlist's songs").
3. Result lists are short pages (10): show 10, "more" pages on scroll/button.
4. Rate limits for dev-mode apps are not published, so: debounce, minimum 2 characters, cache, cancel stale responses.

## Step 0 — API spike (about 30 min, before any UI)

One throwaway script, real account, real log of the JSON:

1. `search(type="track,playlist", limit=10)` — confirm the cap, `offset` paging, the shape of `playlists.items` (Spotify can return `null` entries), and image sizes present.
2. `current_user_playlists(limit=50)` — fields, images, `tracks.total` (renamed? `items.total`?).
3. `playlist_items` on one owned playlist and one followed playlist — confirm the owned/followed difference and the `item` field name.
4. Liked songs endpoint under the new rules (decides whether the optional phase is cheap).
5. Time each call; check the `Retry-After` handling already in `_check_rate_limit`.

## Where the UI lives

Recommended: a **new dashboard widget "Spotify Browser"** (`src/ui/clSpotifyBrowserWidget.py`), opened from a search button next to the Up Next / lyrics / App Volume circles (or the dock), spawned and persisted like the other widgets (`spawn_widget`, saved geometry, per-monitor layouts — so it gets the overlap-packing for free).

Why not a fourth drawer in the media widget: the drawers are narrow single-column lists that grow the widget downward; a result list with covers, two tabs and a search box needs real width and height, and keeping the player compact matters more.

Layout (design px, scaled by `UIScaler` as every widget):

```
[ search box ............................ x ]   [My playlists | Spotify]
 ┌───┐ Song title                          3:21   ▶  +
 │ ▣ │ Artist · Album
 └───┘
 ┌───┐ Playlist name                       42 tracks  ▶  >
 │ ▣ │ by owner
 ...
```

- A **QListView with a custom delegate** (model-backed), not one widget per row: far cheaper to scroll and to repaint, and covers load lazily only for visible rows. Rows are painted with the theme palette (skins recolour through `Theme`).
- Row actions on hover/selection: **Play** (a song: `uris=[uri]`; from inside a playlist: `context_uri` + `offset` so it keeps playing the rest of that playlist), **Add to queue**, and for a playlist **Open** (drill in to its songs where allowed) or **Play**.
- Keyboard: type to filter, Up/Down to move, Enter = play, Shift+Enter = add to queue, Esc = clear / back out of a playlist. Click on a row = play, matching the Up Next rows.
- Empty/loading/error states are written out ("Searching...", "No results", "Spotify is rate limiting, try again in a moment", "Can't list this playlist's songs").

## Data flow (MQTT only, as everywhere)

UI to service, on the existing `pc/spotify/control` via `spotify.control` (schema in `config/actions.json` gains `query`, `kinds`, `offset`, `request_id`, `playlist_id`, `context_uri`, `offset_uri`):

| Action | Does | Answer |
|---|---|---|
| `library` | list your playlists (cached; refetch on demand or after N minutes) | retained `jarvis/sys/spotify_library` `{playlists:[{id,uri,name,owner,count,image,owned}], fetched_at}` |
| `search` | one `sp.search` page | `jarvis/sys/spotify_search` `{request_id, query, offset, tracks:[...], playlists:[...], has_more}` (not retained) |
| `playlist_tracks` | the songs of an owned/collab playlist (paged) | `jarvis/sys/spotify_playlist` `{request_id, playlist_id, items:[...], has_more}` or `{error:"not_available"}` |
| `play_uri` | start a song (`uris`) or a context (`context_uri`, optional `offset_uri`) | the usual `jarvis/feedback` |
| `queue_add` | `add_to_queue(uri)` | feedback; then the queue refresh path already used by Up Next |

- The UI ignores any answer whose `request_id` is not the latest it sent (typing fast never shows stale results).
- Debounce 350 ms in the widget, minimum 2 characters; `clSpotify.py` keeps a small LRU of `(query, offset)` pages for a minute so backspacing is free, and uses the existing `_check_rate_limit` / backoff.
- Search work runs in a worker thread (`asyncio.to_thread`), never in the MQTT loop, same as `refresh_queue`.
- Local filtering of "My playlists" happens in the UI on the retained library message: zero API cost while typing.
- Payloads are trimmed to what a row paints (id/uri, title, artist, album, duration, one image url). No base64 images over MQTT.

## Artwork

- Done by the UI with `QNetworkAccessManager` (async, off the paint path) and a `QNetworkDiskCache` under `data/cache/spotify_art/` (gitignored, per machine), plus an in-memory LRU of ~100 `QPixmap`s. Only rows that become visible request their image; a placeholder (theme-coloured note/disc glyph) shows until it arrives.
- Image choice done in the service (smallest `images[]` entry ≥ 96 px wide), so the UI downloads ~5–15 KB per cover.
- Works the same on Windows and Linux (Qt networking, no OS code). If the machine is offline or the host blocked, the placeholder simply stays.

## Auth

No new scope for phase 1–3 (read scopes already cover search, playlists and queue-add via `user-modify-playback-state`). **Liked Songs** needs `user-library-read`: that is the same one-time re-consent as the reorder idea, and the same rule applies: the service must not start an interactive OAuth flow on a scope change (it would hang a background process); add a foreground `python src/clSpotify.py --auth` first. Per machine.

## Phases

1. **Spike** (above). Decide on Liked Songs and on the owned-only drill-in from facts.
2. **Backend**: `library`, `search`, `play_uri`, `queue_add`, `playlist_tracks`, the LRU/cache, payload trimming; actions schema; tests in `tests/test_clSpotify.py` (paging, `null` playlist entries, the `item` field, not-owned playlist gives `not_available`, cache hit makes no request, rate-limit block, play with `context_uri` + `offset`).
3. **Widget skeleton**: search box, the two tabs, the delegate, My-playlists local filter, play/queue buttons, keyboard, states; `tests/test_clSpotifyBrowserWidget.py` (debounce, stale answer ignored, filter, Enter / Shift+Enter dispatch, empty and error states, hidden-widget does nothing).
4. **Wire into the dashboard**: button next to the media circles (or dock icon), `spawn_widget`, saved geometry/layout; `tests/test_clUI.py` additions.
5. **Artwork**: network + disk cache + lazy visible-row loading; test with a fake reply (no real network in tests).
6. **Playlist drill-in** for owned/collab playlists (songs list, play-from-here with `offset`).
7. **Optional**: Liked Songs tab (after `--auth`), voice entry ("search Spotify for ..." opens the widget prefilled), recent searches.

Docs: `docs/context/ui.md` (the widget, delegate rules, art cache), `docs/context/cross-platform.md` (the new actions and topics, the owned-only limit, the page-of-10 cap).

## Open questions for you

- New **widget** as recommended, or a drawer inside the media widget?
- Is searching the **whole Spotify catalogue** wanted, or only your own playlists (then phase 2 shrinks a lot: no search endpoint, no paging)?
- Should clicking a song **play it right away** (like the Up Next rows) or show Play / Add-to-queue buttons first?
- Do you want **Liked Songs** (needs the re-login step)?

Sources: [February 2026 migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide), [Search for Item](https://developer.spotify.com/documentation/web-api/reference/search), [Update Playlist Items](https://developer.spotify.com/documentation/web-api/reference/reorder-or-replace-playlists-items).
