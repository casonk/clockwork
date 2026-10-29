# clockwork

Shared cron, systemd, and macOS launchd scheduling helpers for the portfolio.

`clockwork` exists to pull scheduler concerns out of repo-local shell snippets and
unit-file templates. Repositories should keep their workload logic, env files,
and domain-specific wrappers. `clockwork` owns the declarative job manifest,
rendered scheduler artifacts, and install-time guidance for cron, systemd, and
user LaunchAgents.

## Scope

`clockwork` currently handles:

- concrete `systemd` services and timers for user or system scope
- owner-only macOS user LaunchAgents with deterministic labels and plist output
- interval timers (`OnBootSec` / `OnUnitActiveSec`) and calendar timers
- cron examples rendered from the same manifest shape
- safe file installation into a unit directory or crontab snippet file
- a Flask web UI for browsing, enabling, and disabling jobs across all example manifests
- mTLS provisioning via `scripts/setup-mtls.sh` and `scripts/setup_caddy.py`

`clockwork` does not yet try to own:

- repo-specific workload wrappers such as notification hooks or env bootstrap
- instance-template units such as `name@.service`

Repos with parameterized template units can either render concrete units through
`clockwork` or keep the template local until `clockwork` grows first-class
template support.

The tracked manifests under `examples/` use placeholder paths, usernames, and
config locations on purpose. They show the scheduler shape without publishing
machine-specific local paths or host identities.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
```

## Web UI

A Flask app (`web/app.py`) provides a browser interface for enabling, disabling,
and monitoring all jobs defined under `examples/`.  It is served behind Caddy
with mTLS and proxied over WireGuard. The standalone app now binds to
`127.0.0.1` by default; non-loopback exposure is an explicit opt-in.

```bash
# Development
python3 web/app.py

# Production (systemd user service, managed by its own clockwork manifest)
systemctl --user start clockwork-web.service
```

On macOS, the web UI automatically uses `launchctl` for user-scope status and
control instead of invoking Linux-only `systemctl`/`journalctl`. System-scope
launchd operations fail closed because Clockwork does not install LaunchDaemons.
The macOS profile uses loopback port `5001`; port `5000` is commonly occupied by
AirPlay Receiver. Per-job controls install only the exact selected job, including
when a manifest contains multiple jobs, and bulk controls honor a sibling
`*.local.toml` override just like individual controls do. The UI records an
enabled-state change only after every required scheduler command succeeds.
Switching one enabled job between native scheduling and cron is transactional:
Clockwork restores the old target if the new one fails, and marks the job off
if that rollback also fails.

A loaded LaunchAgent is never treated as refreshed merely because its plist on
disk matches the current manifest; launchd may still hold older in-memory
state. Disable it first and then enable it to perform the reviewed
reinstall/bootstrap flow. A systemd-rich
downstream manifest that intentionally fails launchd rendering can be enabled
only when an owner-only, exact-label runtime adapter is already installed;
Traction Control adapters must also identify the exact job through their
reviewed runtime wrapper. When Clockwork wraps that command to load private
environment files, validation accepts only Clockwork's exact four-argument
loader envelope and inspects the nested runner arguments.

If you intentionally expose the Flask app beyond loopback, set
`CLOCKWORK_WEB_ALLOW_REMOTE=1`. Remote exposure without client-authenticated
TLS is blocked unless you also set `CLOCKWORK_WEB_ALLOW_REMOTE_WITHOUT_MTLS=1`.

For agentic maintenance manifests that carry provider/model defaults in
`[jobs.environment]`, the edit modal now surfaces those defaults directly so
they can be changed without manually editing TOML. Optional local-only
credential/profile overrides still belong in `environment_files`. Interval
timers expose their boot delay and repeat cadence in the same edit modal.

System-scope jobs (`scope = "system"`) require elevated writes.  The app uses a
sudoers drop-in (`config/sudoers/clockwork-web`) and a wrapper script
(`config/scripts/clockwork-system-install`) so only the specific commands needed
run as root.

### Media library

`web/media_library.py` keeps the watch folder (`CLOCKWORK_WATCH_DIR`) in
Jellyfin/Plex layout and builds a searchable metadata catalog:

```text
watch/movies/Comedy/Napoleon Dynamite (2004)/Napoleon_Dynamite_2004_1080p_Jared_Hess.mp4
watch/movies/Comedy/Napoleon Dynamite (2004)/Napoleon_Dynamite_2004_1080p_Jared_Hess.en.srt
watch/movies/Crime/Goodfellas (1990)/Goodfellas_1990_1080p_Martin_Scorsese_Remastered.mkv
watch/shows/Breaking Bad (2008)/Season 01/Breaking_Bad_S01E01_1080p.mkv
```

Folders stay `Title (Year)` so media servers keep matching them. Files are
`NAME_DATE_QUALITY_DIRECTOR[_EDITION]` (episodes `SHOW_SxxEyy_QUALITY`) with
every space written as `_`. Up to two directors are joined with `_&_`, a
missing value is written `Unknown` so the fields keep their positions, and an
edition (Extended, Director's Cut, …) is recognized by name as the last
field. Subtitles and audio tracks share the video's stem
(`….en.srt`, `….ac3`). Each `sync` renames existing files that drift from the
convention, taking directors from the catalog.

Movies sit in one genre folder each (Action, Adventure, Animation, Comedy,
Crime, Documentary, Drama, Family, Fantasy, Horror, Musical, Mystery, Romance,
Sci-Fi, Thriller, War, Western, Other). Wikidata genre tags are unordered and
noisy, so a local Ollama model (`CLOCKWORK_GENRE_MODEL`, default `qwen3:14b`,
at `CREW_CHIEF_URL`) picks the primary genre from the title, year, director,
lead cast and tags; nothing is sent beyond the local model. Documentary and
animation tags decide directly, and a keyword fallback applies when the model
is unreachable. Titles already inside a genre folder are never moved again,
so moving a folder by hand (e.g. in File Browser) overrides the choice. Shows
stay under `shows/`.

```bash
python3 web/media_library.py organize            # dry run: print the rename plan
python3 web/media_library.py organize --apply    # rename, journaled per batch
python3 web/media_library.py undo <batch>        # reverse one batch
python3 web/media_library.py scan                # refresh the catalog
python3 web/media_library.py sync                # organize --apply, then scan
python3 web/media_library.py search --director fincher --year-from 1990
```

- Titles, years and canonical names come from Wikidata (no key needed). With
  `CLOCKWORK_TMDB_API_KEY` set, TMDB adds plots, ratings and fills any gaps.
  Only parsed titles and years are sent to either service. Lookups are cached
  in the catalog; titles with no match are retried weekly.
- Resolution, codecs, channels, runtime and stream languages come from local
  `ffprobe`; quality labels count widescreen crops (1920x800) as 1080p.
- Entries Transmission still tracks (asked through Magneto's `/api/torrents`)
  are never renamed, since that would break seeding. If Magneto is
  unreachable, anything owned by the `transmission` user is skipped instead.
- Every title gets its own folder. Multi-movie packs are split into one
  folder per film, and feature films inside a show pack (`S05M01 ...`) are
  filed under `movies/`. Subtitles and external audio tracks (`.ac3`, `.dts`,
  ...) follow their video. Show videos without an episode code (including
  `SxxXyy` bonus codes) go to the show's `Extras/` folder.
- Release clutter (ads, `.txt`/`.nfo`/`.jpg`, samples, duplicate episode
  copies) is never deleted: it moves to a hidden `.release/` folder inside the
  title (or `watch/.release/<pack>/` for split packs). Multi-part movies
  (CD1/CD2) are skipped and reported. Existing targets are never overwritten,
  and `undo` also removes the folders a batch created.
- Shows without a director list their creators in the director field.
- The catalog (`config/media-library.sqlite`) and rename journal
  (`config/media-library-renames.jsonl`) are local and gitignored.

On **To Watch**, searching a TV series groups torrent results by season and
labels full-season and multi-season packs. Select several distinct episodes and use **Add
selected episodes** to send their magnet links to the local Magneto in one
action. Each release stays a separate Transmission torrent; the picker blocks
overlapping episodes and season packs. After the downloads finish, **File
completed episodes** uses Magneto's local torrent status and links matching
videos into `watch/shows/Title (Year)/Season NN/`. It links subtitles with
matching stems, leaves Transmission's source files in place for seeding, and
skips incomplete torrents and episodes already filed. It uses each completed
torrent's current directory, including files Magneto moved to its configured
library directory. The action is explicit and does not move or rename data.

Library pages (`web/library_pages.py`):

- `/library/title/<path>`: one page per movie or show with director/creator,
  cast, genres, IMDb/Wikidata links, every file's quality, codecs, runtime,
  subtitle and audio tracks, episodes grouped by season, extras, and a
  genre-folder dropdown that moves a movie (journaled; sync keeps the choice).
  Show pages also compare local files with TVmaze's aired episode guide, label
  gaps aired in the last 30 days as new and older gaps as missing, and search
  matching releases through Magneto. The
  backing `GET /api/library/episodes/shows/<title-folder>` endpoint returns
  show details, per-episode status, and counts. Ambiguous show names require
  choosing a TVmaze show ID; future episodes are counted but not downloadable.
- `/library/play/<file>`: plays a video in the browser with its `.srt`
  subtitles (and embedded text subtitles) converted to WebVTT. MP4 with
  H.264/AAC plays directly with HTTP range seeking. Everything else (MKV,
  HEVC, 10-bit, AC3/DTS audio, AVI) streams as HLS from `web/streaming.py`:
  the player gets a playlist for the whole file in 6-second segments and
  ffmpeg transcodes them on demand to H.264 + stereo AAC, on the GPU with
  NVENC when available (x264 otherwise). Seeking past what is converted
  restarts ffmpeg at that point; forced IDR frames and an output timestamp
  offset keep segments from different runs aligned. A transcoder pauses when
  it is 40 segments ahead of the viewer (also after the tab closes), and
  sessions idle for 10 minutes are removed. Segments live in
  `CLOCKWORK_STREAM_CACHE` (default `~/.cache/clockwork/library-hls`), at most
  three sessions at a time. hls.js (pinned, SRI-checked) drives playback;
  browsers without Media Source Extensions fall back to native HLS.
  A Quality menu offers Original (direct play when possible, otherwise the
  source resolution at up to 12 Mb/s, 16 Mb/s for 1440p and 30 Mb/s for 4K
  sources) and every lower step of 1440p, 1080p (8 Mb/s), 720p (4 Mb/s),
  480p (1.5 Mb/s) and 360p (0.8 Mb/s). Switching continues from the same moment, stops the
  previous quality's transcoder, and is remembered per browser.
- The ECU (`ecu`) observes the active HLS `ffmpeg` process as GPU activity. Set a
  matching `[[activities]]` entry in its local config with `process = "ffmpeg"`,
  `priority = "live"`, and `preempt = true`. Lower-priority ECU leases then
  wait for playback or preempt their command when opted in with
  `--preemptible`.
- `/library/browse/`: the folder tree as it is on disk, hidden `.release/`
  folders excluded, with play and download links. Catalog ratings and short
  descriptions appear beside title folders when available. Show folders use
  TVmaze as a read-only fallback when those catalog fields are empty.
- `/library/manage`: start a sync (`systemctl --user start` of
  `clockwork-media-library.service`), see its recent log and timer state,
  the items needing attention (still seeding, duplicate copies, no metadata),
  and the recent rename batches with the undo command.

### Read and listen libraries

Set `CLOCKWORK_READ_LIBRARY_DIR` and `CLOCKWORK_LISTEN_LIBRARY_DIR` in the
Clockwork web service environment to readable local folders. The folders are
optional and can be configured independently. To Read and To Listen then check
for matching local titles when opened, link directly to a viewer or player,
and offer a Magneto search for missing titles. The search and add APIs recheck
the configured library before sending a magnet, so a title acquired between
search and click is blocked. Without a configured root, downloads from these
list pages are disabled because duplicate detection is unavailable.

To Read also looks up exact title matches in the Open Library Search API and
shows author, first publication year, cover, and a catalog link. These details
remain available even when the read library is not configured. Download search
still uses torrents-csv by default. To add more torrent indexers for To Read and
To Listen, configure `CLOCKWORK_PROWLARR_URL` and
`CLOCKWORK_PROWLARR_API_KEY` in the web service environment. Clockwork asks
Prowlarr for book or music results, merges magnet links with torrents-csv by
info hash, labels their sources, and passes a selected magnet to Magneto.
Only magnet results are shown; direct book files and Usenet releases cannot
be sent through Magneto's current add endpoint. An unavailable Prowlarr or
Open Library response leaves the existing torrent search usable.

To Read also searches arXiv for items in the Papers and Articles lists and
Internet Archive text items for any reading list. arXiv matches link to the
abstract and PDF. Internet Archive matches link to the item page, where the
Archive shows the item's actual read, borrow, and download options. These
sources are available without a local library folder; they do not pass
through Magneto or automatically save files into the local library.

`/media/read/` browses PDF, EPUB, TXT, and Markdown files. PDFs and text open
in the browser; EPUB uses pinned JSZip and epub.js bundles with scripted book
content disabled. `/media/listen/` browses MP3, M4A, AAC, FLAC, OGG, Opus,
and WAV files. It plays an audio file or a folder playlist with HTML audio;
browser codec support varies, and every file has a download fallback. File
routes refuse hidden paths, symlinks, and paths outside the configured root.

### AI super-resolution (experimental)

`web/upscale.py` renders an AI-upscaled copy of every library video ahead of
time with Real-ESRGAN on the GPU: sources up to 576 lines become 1080p, 720p
and 1080p sources become 4K (the model runs at 4x, then the frame is resized to
the exact target). Animation (the Animation genre folder or "animated" genres)
uses `realesr-animevideov3`; live action blends `realesr-general-x4v3` with its
weak-denoise twin (`CLOCKWORK_UPSCALE_DENOISE`, default 0.5) so film grain and
skin texture survive. The queue runs lowest resolution first, newest downloads
first within that.

Renders are HEVC (BT.709) with the audio re-encoded to AAC, under the hidden
`<watch>/.upscaled/` tree mirroring the library, so names and folders stay as
they are. They show up as "✨ AI 1080p/4K (experimental)" in the player's
Quality menu and stream through the same on-demand converter. The worker stops
starting new renders when free space on the library disk would fall below
`CLOCKWORK_UPSCALE_MIN_FREE_GB` (default 1000) and rechecks hourly; failures
are listed in `config/media-library-upscale-failed.json` and skipped.

Rendering is chunked. One decoder streams the whole video, and the output is
encoded in chunks of whole 6-second streaming segments, each sized for about
`CLOCKWORK_UPSCALE_CHUNK_WORK_SECONDS` (60) of GPU work, into
`<render>.chunks/` with a manifest. A stop, restart or reboot loses at most the
chunk in progress, and the next run resumes there. The resume point is found
by the content of the last frames, because timestamp seeking lands a frame
early or late depending on the container. Interrupted renders continue before
new ones start. While a render is in progress, the player already offers the
finished part ("✨ AI 4K (rendering: first 25:00)"); reload later for more.
The finished chunks are joined and the audio encoded once from the source,
because encoding it per chunk would add up to drift.

The worker is throttled so it does not run the machine hot or loud: it works
only `CLOCKWORK_UPSCALE_DUTY` of the time (default 0.35) and rests the rest
between chunks, with the encoder closed so the GPU drops from ~120 W (P2, held
by an open NVENC session) to ~20 W (P8) within about 30 seconds,
pauses when the GPU reaches `CLOCKWORK_UPSCALE_MAX_GPU_C` (72) or the CPU
package `CLOCKWORK_UPSCALE_MAX_CPU_C` (78, a median over a few seconds since
Intel package readings spike) until both are 6 C cooler, and limits ffmpeg and
PyTorch to `CLOCKWORK_UPSCALE_THREADS` (2) CPU threads. The Manage page shows
the duty cycle and any cooling pause.

Unattended library renders use low CPU and I/O priority. A render requested
from a title or player page moves to the front of the queue, starts the worker
if needed, and causes the current render to yield after its next saved chunk.
Title-wide requests keep episode order. The render resumes from its saved
manifest after the requested work completes. Each episode render acquires an
ECU lease: unattended work uses `batch` priority, requested work uses
`interactive`, and both are preemptible by higher-priority live playback. The
systemd service is only the queue coordinator; its pause-file consumer
cooperatively yields when an external lease needs the GPU. The episode render
skips that consumer for its own lease. If the ECU is unavailable, unattended
work falls back to low OS priority.
After a preemption the worker does not retry at once: it asks
`ecu retry-after --json`, which rate limits preemptible leases with AIMD, and
pauses (state `throttled`) until then, waking early for a render request.

To configure cooperative yielding, add a `pause-file` consumer to
the ECU's `config.toml` (under `~/.config/` in the ECU's own directory):

```toml
[[consumers]]
name = "clockwork-upscale"
kind = "pause-file"
unit = "clockwork-upscale.service"
process = "/.local/share/clockwork/upscale-venv/bin/python"
pause_file = "~/.cache/clockwork/upscale-pause"
acknowledgement_file = "~/.cache/clockwork/upscale-paused"
pause_timeout_s = 180
```

Keep the paths aligned with `CLOCKWORK_UPSCALE_PAUSE_FILE` and
`CLOCKWORK_UPSCALE_PAUSED_FILE` in `config/upscale.env.local`. The worker saves
the current chunk, exits its render subprocess to release GPU memory, then
acknowledges and waits for ECU to remove the pause request. It also acknowledges
requests while idle, throttled, or waiting for disk space. Its own per-render
lease uses `--skip-consumer clockwork-upscale`, so it does not pause itself.
It also passes the same pause path to ECU as a pre-start cancellation file,
allowing the coordinator to acknowledge an external request while its own
render lease is waiting for admission, resources, or another lease.
The `process` value should match the full Python executable path reported by
`nvidia-smi` (the suffix shown works with the example Clockwork unit). After
installing the config and environment settings, restart `clockwork-upscale`.

Measured on an RTX 3090: unthrottled, 360p animation runs about 85 fps (3x
real time) and 1080p live action to 4K about 6 fps, at 300 W and over 90 C.
With the defaults, 360p animation runs about 22 fps at roughly 150-180 W with
the GPU holding near 70 C.

Setup, outside the repo:

```bash
python3 -m venv ~/.local/share/clockwork/upscale-venv
~/.local/share/clockwork/upscale-venv/bin/pip install torch spandrel numpy
mkdir -p ~/.cache/clockwork/upscale-models && cd ~/.cache/clockwork/upscale-models
for m in realesr-animevideov3 realesr-general-x4v3 realesr-general-wdn-x4v3; do
  curl -LO "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/$m.pth"
done
sha256sum -c <<'SUMS'
b8a8376811077954d82ca3fcf476f1ac3da3e8a68a4f4d71363008000a18b75d  realesr-animevideov3.pth
8dc7edb9ac80ccdc30c3a5dca6616509367f05fbc184ad95b731f05bece96292  realesr-general-x4v3.pth
1641f8c4464b9f097c9fdda5589273713f67cf59f3d909e0bd688f0cee269dca  realesr-general-wdn-x4v3.pth
SUMS
```

All settings are environment variables, documented with their defaults in
`config/upscale.env.example`; copy it to the gitignored
`config/upscale.env.local` (the service loads it) and restart
`clockwork-upscale` after editing.

`examples/clockwork/upscale.toml` runs `upscale.py run` as an always-on user
service at `nice 19` / idle IO; the Manage page shows its progress and starts or
stops it. `python3 web/upscale.py queue` lists what is left.

Paths are resolved inside `CLOCKWORK_WATCH_DIR` only; anything outside it or
hidden returns 404.

The `/library` page searches the catalog by text, director, actor, genre,
genre folder, type, quality, codec and year range, sorted by title, year
(newest first), recently downloaded (newest file modification time, which
moves and renames keep), rating or size; `GET /api/library` returns the same data
as JSON. `examples/clockwork/media-library.toml` runs `sync` every 30 minutes.

## mTLS and Caddy

mTLS provisioning, Caddyfile generation, and DNS setup live in the
`wiring-harness` repo (`../wiring-harness`).  See its README for the full
provisioning sequence.

## CLI

Render planned scheduler artifacts to stdout:

```bash
clockwork render --manifest examples/archility/archility-weekly.toml --target systemd-user
clockwork render --manifest examples/fedora-debugg/crash-snapshot.toml --target systemd-user
clockwork render --manifest examples/example-scheduler/intraday-snapshots.toml --target systemd-user
clockwork render --manifest examples/example-scheduler/monthly-controller.toml --target cron
clockwork render --manifest examples/shock-relay/gmail-digest.toml --target systemd-user
clockwork render --manifest examples/traction-control/bug-sweep-agentic.toml --target systemd-user
clockwork render --manifest examples/traction-control/template-consolidation-agentic.toml --target systemd-user
clockwork render --manifest examples/traction-control/ci-repair-agentic.toml --target systemd-user
clockwork render --manifest examples/clockwork/clockwork-web.local.toml --target launchd-user
```

Write scheduler artifacts to a target directory or file:

```bash
clockwork install --manifest examples/archility/archility-weekly.toml --target systemd-user
clockwork install --manifest examples/fedora-debugg/crash-snapshot.toml --target systemd-user
clockwork install --manifest examples/snowbridge/wireguard-endpoint-monitor.toml --target systemd-system
clockwork install --manifest examples/shock-relay/gmail-digest.toml --target systemd-user
clockwork install --manifest examples/example-scheduler/monthly-controller.toml --target cron --output /tmp/example-scheduler.crontab
clockwork install --manifest examples/traction-control/bug-sweep-agentic.toml --target systemd-user
clockwork install --manifest examples/traction-control/template-consolidation-agentic.toml --target systemd-user
clockwork install --manifest examples/traction-control/ci-repair-agentic.toml --target systemd-user
clockwork install --manifest examples/clockwork/clockwork-web.local.toml --target launchd-user
# Select one exact job when a manifest contains multiple jobs.
clockwork install --manifest examples/intake/report-and-daemon.toml --target systemd-user --job intake-daemon
```

### macOS Clockwork web LaunchAgent

The tracked macOS manifest is deliberately a non-installable template: it has
no host path or secret. Prepare the private host-specific files first:

```bash
bash scripts/bootstrap_clockwork_web_macos.sh
cp config/macos/clockwork-web.toml.example examples/clockwork/clockwork-web.local.toml
# Replace every /absolute/path placeholder in the ignored local manifest.
clockwork render \
  --manifest examples/clockwork/clockwork-web.local.toml \
  --target launchd-user
clockwork install \
  --manifest examples/clockwork/clockwork-web.local.toml \
  --target launchd-user
```

The bootstrap creates `.venv`, installs the web dependencies, and generates an
owner-only `~/.config/clockwork/clockwork-web.env` without printing its secret.
The runtime wrapper refuses an old/missing Python, missing Flask or tomlkit, a
missing/short secret, or a non-loopback bind. The launchd environment loader
also refuses symlinks, non-regular files, files owned by another user, and any
group/world-accessible secret file. Startup cron autogeneration is disabled by
default so importing or launching the web process cannot mutate tracked
manifests; set `CLOCKWORK_WEB_AUTOGENERATE_CRON=1` only for an explicit,
reviewed migration.

### Schedule (`/schedule`)

The Services menu and Home link **Schedule**: every systemd timer, user and
system scope, soonest first, with when it fires next, how long that is from now,
when it last ran, and the outcome of the service it starts (`ok`, `running`,
`failed (exit-code)`, or `never run`). Each job links to its log. `/api/schedule`
returns the same rows as JSON. It reads `systemctl list-timers` and `show`, so it
needs no elevation. macOS (launchd) hosts are not covered yet.

### Machine load (`/ecu`)

The Services menu links **Machine load**, which renders `ecu status --json`:
the held lease with its priority, mode and GPU size, GPU memory and processes,
and whether each guarded timer job would run or defer. `/api/ecu` returns the
same document (503 with an `error` when unavailable). The page finds `ecu` via
`CLOCKWORK_ECU_BIN`, then `PATH`, then `~/.local/bin/ecu`.

### Optional external personal-list authority

Clockwork's **To Code** list is available from `/home` and the Lists menu at
`/to-code`. It groups backlog items by repository family and repository, with
an explicit family selector when adding an item. Its default store is the
gitignored `config/to-code.json`; `CLOCKWORK_CODE_LIST_FILE` and
`CLOCKWORK_CODE_LIST_HISTORY_FILE` can point to other local files. Importing
repo `BACKLOG.md` entries is a one-time local snapshot. Keep each repo's tracked
`BACKLOG.md` current when completing an item; the page does not write back to
those files.

Clockwork keeps JSON-file list storage by default. To move the To Do, Code,
Watch, Read, Listen, and Invest compatibility routes onto an external SQLite storage
authority, install a reviewed adapter package that exposes
`ClockworkPersonalListAdapter`, `PersonalListSpec`, and `SQLiteResourceStore`,
then set these owner-only environment values:

```text
CLOCKWORK_LIST_STORE_MODULE=<import path of the adapter package>
CLOCKWORK_LIST_STORE_DB=/absolute/path/to/clockwork-personal-lists.sqlite3
CLOCKWORK_LIST_STORE_ORIGIN=air
```

The adapter package is named only in local configuration, never in this
repository, so a private storage backend stays a local deployment choice rather
than a hard-coded public dependency. The origin is an explicit trusted writer
identity, not browser input. With the variables set, existing form routes read
and reconcile the same JSON shape via the adapter's revision-guarded resources.
Leave `CLOCKWORK_LIST_STORE_DB` unset to retain the legacy file store. Do not
point the database at a network share; use a local path or configure a reviewed
rqlite-backed deployment separately. Perform the adapter's validated import of
existing JSON before enabling the setting; route-level cutover remains an
operator-reviewed deployment step.

`install` writes an owner-only plist to `~/Library/LaunchAgents` and prints the
exact `launchctl bootstrap` command for review. It does not activate the job.
LaunchAgent stdout/stderr go to `~/Library/Logs/Clockwork` so the web UI can
provide read-only logs without `journalctl`.

Launchd rendering intentionally rejects fields that cannot be mapped without
changing behavior: user/group identity changes, systemd ordering dependencies,
delayed-login `on_boot_sec`, timer randomization/accuracy, non-default install
targets, system scope, and unsupported restart policies. Supported calendar
forms are daily, weekday, stepped-hour, and stepped-minute schedules. A
stepped-minute calendar such as `*:0/5` becomes one `StartCalendarInterval`
entry per minute with no hour, so it repeats every hour. Windows renders it as
a daily trigger repeating every step, and refuses a step that does not divide
60, where a fixed repetition would drift from systemd's hourly restart.
Supported interval jobs use `on_unit_active_sec` without `on_boot_sec`.

## Manifest Shape

```toml
[[jobs]]
name = "archility-weekly"
description = "Archility weekly architecture audit and diagram render"
scope = "user"
service_type = "oneshot"
working_directory = "/path/to/portfolio/util-repos/traction-control"
exec_start = "/path/to/portfolio/util-repos/traction-control/scripts/archility-weekly.sh"
after = ["network.target"]
start_limit_interval_sec = "0"
environment_files = ["-/path/to/local-only.env"]

[jobs.environment]
PORTFOLIO_ROOT = "/path/to/portfolio"
ARCHILITY_CMD = "/path/to/venv/bin/archility"

[jobs.timer]
kind = "calendar"
on_calendar = "Sun *-*-* 02:00:00"
persistent = true
```

Supported manifest sections:

- `[[jobs]]`: one logical scheduled job
- service keys support standard execution metadata such as `working_directory`,
  `after`, `wants`, restart policy, environment variables, and
  `start_limit_interval_sec`
- `launchd_label`: optional explicit downstream-owned label. It must be
  dot-qualified and end with the exact job name; rendering and web controls use
  the same resolver.
- `launchd_run_at_load`: optional explicit launchd login-start behavior. It
  does not make `on_boot_sec` portable; that field remains fail-closed.
- `start_condition`: optional command, or list of commands, rendered as systemd
  `ExecCondition=`. A condition that exits 1-254 skips the run without failing
  it, so a guard such as traction-control's `job_guard.py check` travels with
  the rendered unit instead of through a drop-in.
- `time_limit`: optional wall-clock bound for one run, rendered as
  `TimeoutStartSec=` for a oneshot service and `RuntimeMaxSec=` otherwise.
  launchd and Task Scheduler have no equivalent for either field, so those
  targets refuse them until `[jobs.launchd]` or `[jobs.windows]` clears them
  (`start_condition = []`, `time_limit = ""`).
- `environment_files`: optional systemd `EnvironmentFile=` paths; use `-...`
  when the file is local-only and may not exist on every machine
- `poll_interval`: optional UI hint for long-running daemons with an internal poll loop
- `[jobs.environment]`: service environment variables
- `[jobs.timer]`: optional scheduler metadata; launchd accepts only the strict
  portable subset described above. Ordering dependencies, delayed login, and
  accuracy/randomized delay are never silently dropped.
- `[jobs.launchd]` / `[jobs.windows]`: optional per-target overrides, applied
  only when rendering that target and invisible to every other rendering
- `[jobs.cron]`: optional cron rendering metadata

### Writing one manifest for Linux, macOS and Windows

Ordering dependencies and timer jitter have no launchd equivalent, and
clockwork refuses to render a job declaring them rather than dropping them and
quietly changing what the schedule means. That refusal is right, but on its own
it makes any manifest using `after` un-installable on macOS — however
Linux-specific that one line is.

`[jobs.launchd]` withdraws a setting for that target instead of deleting it for
everyone. Keys there replace the job's values; an empty value clears a field.

```toml
after = ["network.target"]
exec_start = "/usr/bin/bash scripts/report.sh"   # no such path on macOS

[jobs.timer]
randomized_delay_sec = "600"

[jobs.launchd]
after = []                                       # launchd has no ordering deps
exec_start = "/bin/bash scripts/report.sh"       # the macOS system shell

[jobs.launchd.timer]
randomized_delay_sec = ""                        # launchd cannot express jitter

[jobs.windows]
after = []                                       # nor does Task Scheduler
exec_start = "C:\\Windows\\System32\\cmd.exe /c %h\\bin\\report.cmd"
```

Note there is no `[jobs.windows.timer]` above: Task Scheduler *can* express
jitter, as `RandomDelay`, so `randomized_delay_sec` carries over untouched. Only
withdraw a setting from the targets that genuinely cannot express it.

Unknown keys in these tables are rejected rather than ignored, because a typo
would otherwise fail open — silently doing nothing and leaving the job
un-installable on macOS again. See `examples/portable/weekly-drift.toml`.

`--target` now defaults to the scheduler this platform actually runs
(`launchd-user` on macOS, `systemd-user` on Linux), so one documented command
works on both. Pass `--target` explicitly to override it; `cron` is always
opt-in.

### Platform support

| platform | native target |
| --- | --- |
| Linux | `systemd-user`, `systemd-system`, `cron` |
| macOS | `launchd-user`, `cron` |
| Windows | `windows-user` (Task Scheduler XML) |

All three are rendered natively. Windows is not an emulation layer: clockwork is
a renderer, so supporting Windows means emitting Task Scheduler XML the same way
it emits plists and units. Requiring a container runtime merely to schedule a
job would be a far heavier dependency than the scheduler itself.

A platform with no native scheduler refuses to pick a default and says so,
rather than falling back to systemd and writing units into
`~/.config/systemd/user` on a machine with no systemd to read them -- files that
look installed, never run, and report no error. `--target` still works
explicitly everywhere, which is also how you render `cron` or `systemd-user` for
a container rather than for the host.

### Windows notes

`clockwork install --target windows-user` writes one UTF-16 `.xml` per job and
prints its registration command. Task Scheduler has no drop-in directory, so a
task exists only once `schtasks` has registered it:

```
schtasks /Create /TN "\Clockwork\portable-drift" /XML "%LOCALAPPDATA%\Clockwork\tasks\portable-drift.xml"
```

The files are UTF-16 because `schtasks /Create /XML` rejects UTF-8 task files as
malformed XML.

What Windows can and cannot express, relative to the other targets:

- **jitter works.** Task Scheduler has `RandomDelay`, so `randomized_delay_sec`
  carries over unchanged -- unlike launchd, which cannot express it at all
- **ordering dependencies do not exist**; withdraw `after`/`wants` with
  `[jobs.windows]`
- **environment variables are not supported** by an `Exec` action; set them
  inside the script the task runs
- **paths must be Windows paths.** `%h` becomes `%USERPROFILE%`, which Task
  Scheduler expands at run time, so rendering never needs to know the target
  user's home and produces identical output on any host

See `examples/` for mappings from current portfolio repos.

## Portfolio Mapping

The first migration targets are the repos where scheduling is already explicit:

- a private scheduling consumer: cron examples and systemd-backed refresh flows
  - `examples/example-scheduler/intraday-snapshots.toml` covers the daily intraday snapshot timers plus matching cron snippets
  - `examples/example-scheduler/monthly-controller.toml` keeps the monthly all-accounts cron example
- `intake`: generated user-level daemon and report timer units
- `fedora-debugg`: recurring snapshot workflow that refreshes the tachometer sidecar
- `shock-relay`: shared Gmail notification digest timer
- `snowbridge`: installed system service + interval timer
- `traction-control`: daily governance audit plus daily bug sweep and every-other-day agentic template consolidation and CI repair
- a private orchestrator daemon: repo-local service/orchestrator conventions
  (see `examples/example-orchestrator/orchestration.toml`)

## Development

```bash
ruff check .
ruff format --check .
black --check --diff .
pytest -q
```

## Contributing

See `CONTRIBUTING.md`.

## License

MIT. See `LICENSE`.
