# BACKLOG.md

Portfolio backlog for this repository. Pending items are candidates for execution —
manually or via crew-chief. Entries sourced from archility audit are tagged
`[archility:YYYY-MM-DD]`; manual entries use `[manual:YYYY-MM-DD]`.

The archility weekly job populates this file automatically via `archility audit --write-backlog`.
To execute a backlog item with crew-chief: `crew-chief agent "Work on item: <item text>"`.
Mark items `[x]` when complete and move them to Done.

## Pending

- [ ] [manual:2026-11-11] **Apple Music playlist sync** — Apple Music is link-only today. A
  `MusicKit` adapter in `web/providers.py` (`accounts`/`playlists`/`entries` and
  `create_playlist`/`add` against `api.music.apple.com/v1/me/library`, then `BROWSE`/`WRITE` in
  its capabilities) needs an Apple Developer Program membership for the developer token and an
  Apple Music subscription for the user token, which is re-issued about every six months. Blocked
  until there is a subscription. Apple TV stays link-only (no public watchlist or Up Next API).

- [ ] [manual:2026-11-11] **Cross-service playlist copy with a preview** — copying is limited to
  one service (Spotify to Spotify, YouTube account to YouTube account). Copying between services
  needs title and artist matching, so build it as a preview the user approves before anything is
  added; never add a guessed match silently.

- [ ] [manual:2026-11-05] **Fix the failing `tests/test_site_chrome.py` test** —
  `test_publish_writes_both_files_once_and_skips_a_missing_directory` fails on `main`
  (an assertion at line 73); it predates this month's work and is the only red test in
  the suite.

- [ ] [manual:2026-11-04] **Complete Tradility UI federation** — after the
  standalone Tradility web service is installed and verified, set
  `CLOCKWORK_TRADILITY_WEB_URL` for the Clockwork service, preserve optional To
  Invest/finances provider contracts, and remove Clockwork's duplicated
  Tradility template and domain routes.
  Update 2026-11-05: until then the legacy `/to-tradility` page carries the Live
  control and live cards (#162) and `/api/tradility-quotes` forwards to the
  standalone app; To Invest links each ticker to its card, and
  `CLOCKWORK_TRADILITY_CARD_URL` (a `{ticker}` template) points those links at the
  standalone service once it is federated. Remove all of this with the template.
- [ ] [manual:2026-10-15] **Ask GitHub Support to purge pre-rewrite commits** — this
  repository's published history was rewritten (most recently on 2026-10-15,
  to remove per-commit AI attribution trailers). GitHub keeps serving the old
  commits to anyone with a hash, and through the read-only `refs/pull/*` refs
  of earlier pull requests, until Support garbage-collects them. The request
  is filed once for the whole portfolio from traction-control's backlog; mark
  this done when an old hash returns 404 here. Never push an old clone's
  history back.

- [ ] [manual:2026-11-11] **Strip AI-attribution trailers added after the 2026-10-15 rewrite** — assistant
  co-author and session trailers are back on 62 lines of commit messages on `main`, mostly squash-merge
  messages (#38 to #57 and #190). A pull request cannot remove them, since a merge only adds commits.
  Removing them means rewriting `main`: a trailer-only filter leaves every tree identical, but needs
  the branch ruleset that requires pull requests lifted for one force-push, then a re-sync of every
  clone, then the purge request above covering the new hashes. Until then, put no attribution lines
  in commit messages or PR bodies; GitHub builds the squash message from them.

## In Progress

## Done

- [x] [manual:2026-11-05] **Finances daily series** — a Month-end/Daily toggle on
  `/finances`; daily Schwab balances are rebuilt from order history and anchored to
  statements, with source, raw and adjustment per point. (#157)
- [x] [manual:2026-11-05] **To Invest industries and target ranking** — Sectors and
  Industries cards computed from holdings and the classification CSV (crypto is its own
  industry under Financials), a Grouped / Holding % / Target rank order, and up/down
  arrows to rank cards. (#158)
- [x] [manual:2026-11-05] **Cascading targets** — targets are one tree that sums to 100%;
  a ticker edit rolls up, an industry or sector edit rescales what is beneath it, and
  typed values hold across tiers (impossible combinations are refused). (#159, #160)
- [x] [manual:2026-11-05] **Live Tradility page and To Invest links** — live prices,
  indicators and detail cards on `/to-tradility`, and a link from each To Invest ticker
  to its Tradility card. (#162, #164)

- [x] [manual:2026-11-01] **Let shopping AI recategorize existing items** —
  preserve the safe missing-only action and add an explicit confirmed scope
  that can reconsider every item and replace an existing type label.

- [x] [manual:2026-11-01] **Unify Tradility's manual refresh flow** — replace
  Reload and Run refresh with one Update market data action that follows the
  systemd job, shows elapsed progress, detects the new artifact through a
  lightweight version endpoint, and reloads the view automatically.

- [x] [manual:2026-11-01] **Show qualitative Tradility profiles** — add sourced
  company/fund names, descriptions, classifications, headquarters, websites,
  exchange, employees, entity type, and freshness to exact-ticker details
  without bloating the stock-card collection response.

- [x] [manual:2026-10-31] **Add Tradility stock performance details** — make
  cards open on-demand multi-year charts, period returns, 52-week range,
  transparent technical ratings, indicators, and source/freshness context.

- [x] [manual:2026-10-26] **Add the To Code portfolio backlog list** — group
  items by repository family and repository, require explicit family selection,
  and link the local list from Home and the Lists menu.

- [x] [manual:2026-10-19] **AI upscaler: let the GPU really rest, and make renders resumable** — chunked rendering: chunks of whole 6 s streaming segments (~60 s of GPU work each) with the NVENC session closed between them, so rests reach P8 (~20 W) instead of P2 (~120 W). Manifest-based resume locates the next frame by content (timestamp seeks were off by ±1 frame depending on the container), the audio is encoded once at the join, and renders in progress stream from their finished chunks. ECU pause-file requests now stop cleanly after a saved chunk and resume after the lease.
