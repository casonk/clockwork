# BACKLOG.md

Portfolio backlog for this repository. Pending items are candidates for execution —
manually or via crew-chief. Entries sourced from archility audit are tagged
`[archility:YYYY-MM-DD]`; manual entries use `[manual:YYYY-MM-DD]`.

The archility weekly job populates this file automatically via `archility audit --write-backlog`.
To execute a backlog item with crew-chief: `crew-chief agent "Work on item: <item text>"`.
Mark items `[x]` when complete and move them to Done.

## Pending

- [ ] [manual:2026-10-15] **Ask GitHub Support to purge pre-rewrite commits** — this
  repository's published history was rewritten (most recently on 2026-10-15,
  to remove per-commit AI attribution trailers). GitHub keeps serving the old
  commits to anyone with a hash, and through the read-only `refs/pull/*` refs
  of earlier pull requests, until Support garbage-collects them. The request
  is filed once for the whole portfolio from traction-control's backlog; mark
  this done when an old hash returns 404 here. Never push an old clone's
  history back.

## In Progress

## Done

- [x] [manual:2026-10-26] **Add the To Code portfolio backlog list** — group
  items by repository family and repository, require explicit family selection,
  and link the local list from Home and the Lists menu.

- [x] [manual:2026-10-19] **AI upscaler: let the GPU really rest, and make renders resumable** — chunked rendering: chunks of whole 6 s streaming segments (~60 s of GPU work each) with the NVENC session closed between them, so rests reach P8 (~20 W) instead of P2 (~120 W). Manifest-based resume locates the next frame by content (timestamp seeks were off by ±1 frame depending on the container), the audio is encoded once at the join, and renders in progress stream from their finished chunks. ECU pause-file requests now stop cleanly after a saved chunk and resume after the lease.
