# BACKLOG.md

Portfolio backlog for this repository. Pending items are candidates for execution —
manually or via crew-chief. Entries sourced from archility audit are tagged
`[archility:YYYY-MM-DD]`; manual entries use `[manual:YYYY-MM-DD]`.

The archility weekly job populates this file automatically via `archility audit --write-backlog`.
To execute a backlog item with crew-chief: `crew-chief agent "Work on item: <item text>"`.
Mark items `[x]` when complete and move them to Done.

## Pending

- [ ] [manual:2026-10-19] **AI upscaler: let the GPU really rest, and make renders resumable.** While `clockwork-upscale` rests (65 % of the time at duty 0.35), or is frozen for a benchmark, it keeps its CUDA context and the `hevc_nvenc` session open, so the RTX 3090 stays in P2 (memory 9501 MHz) drawing ~120–140 W at 0 % load. Measured median GPU draw since the throttle went in: 156 W at 67 °C. Real rests (P8, ~25 W) would bring that to roughly 100 W and lower the temperatures the duty cycle is meant to control. Approach: render in chunks of a few minutes, each with its own NVENC session and a checkpoint, and concatenate at the end. Rest between chunks with the encoder closed (check whether an idle CUDA context alone still holds P2; if so, rest in longer blocks, e.g. 2 min work / 4 min idle, after `torch.cuda.empty_cache()`). Chunks also make a stop (service restart, benchmark, reboot) lose at most one chunk instead of the whole video; the tachometer bench could then stop the unit at any time instead of waiting for a job boundary. Verify with `nvidia-smi --query-gpu=pstate,power.draw` during a rest and the tachometer GPU power chart.
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
