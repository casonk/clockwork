# Changelog

## Unreleased
- Guardrails. Grandmaster Clock: critical apps (default `pit-box-guacd` and `pit-box-guacamole`, the remote-desktop path; `CLOCKWORK_GRANDMASTER_CRITICAL` changes the list) need their name typed to be disabled or restarted and are skipped by bulk changes; bulk changes show a dry-run list to confirm first, run one change at a time, and are spaced 10 seconds apart; every change, including Home card changes, is written to the owner-only `config/grandmaster-audit.jsonl` (capped, newest 15 shown on the page, `GET /api/grandmaster/audit`). Git hooks: `scripts/install-git-hooks.sh` installs the pre-commit hooks and a pre-push stage that repeats them on what is pushed and then runs `pytest`. Agent safety: `scripts/guard_destructive_git.py`, wired in `.claude/settings.json`, blocks git commands that rewrite a working tree holding uncommitted changes. `test_site_chrome` no longer depends on the developer's own rendered themes.
- To Eat (`/to-eat`, `/api/eat`): a list of restaurants to try, with Restaurants, Cafes, Bars and Takeout lists, a been/to-try toggle, optional cuisine and place notes, and a 📍 button that opens the name in a map search. Stored in the gitignored `config/to-eat.json` (`CLOCKWORK_EAT_LIST_FILE`). In the Lists menu and on Home.
- Grandmaster Clock also controls the Home page. A Home page cards section lists every Home card by group and can hide or show any of them (Home only; a hidden service stays in the Services menu and keeps running), remove link and placeholder cards, and add link cards (name, icon, group, and an `http(s)` address or a `/path`; `javascript:`, protocol-relative and credential-bearing URLs are refused). The choices are stored in the gitignored `config/home-cards.json` (`CLOCKWORK_HOME_CARDS_FILE`). Until a list has been saved the hidden set is still `CLOCKWORK_HOME_HIDE_SERVICES` (default `tradility-web`). New endpoints: `GET /api/grandmaster/home` and `POST /api/grandmaster/home/{hide,links,remove}`.
- Grandmaster Clock (`/grandmaster`, `/api/grandmaster`): one page to enable, disable and restart the portfolio's long-running apps (services with no timer or cron, not one-shots), grouped by repository with live running, failed, stopped and disabled states, per-app and per-repository buttons, and an activity log. It reuses the scheduler page's enable, disable and restart paths, so state and system-scope sudo behave the same. The running web UI is protected from disable and restart, bulk Restart only touches apps that are running, and bulk actions skip the web UI. In the Services menu and on Home.
- Public metadata lookups are paced: Wikidata stays about a second apart and TMDB about a third of a second apart, with a little jitter. A 429 or 503 pauses that provider for at least 15 minutes, or for the Retry-After it sent (up to six hours). The pause is stored in the catalog, the sync keeps filing, and the next run waits it out. A paused lookup is left unchecked so it is tried again after the pause.
- Finished show downloads are registered in the library while Transmission is still seeding them. The sync links episode files into `shows/Title (Year)/Season NN/` and leaves the torrent data where it is. `magneto-notify` and `clockwork-media-library` also re-arm after a user-manager restart or a skipped run (`OnStartupSec` and `OnUnitInactiveSec`), so a completed pack is moved into the watch folder instead of sitting in the download directory until the timer happens to be restarted.
- The To Watch, To Read, To Listen, and To Invest page header no longer repeats Home and Clockwork. That bar links to the list's own top-level categories.
- To Watch and To Listen can synchronize dedicated private YouTube playlists across multiple Google accounts through owner-only OAuth grants. Sync is additive in both directions: remote videos are imported by stable YouTube ID, and active local rows with an explicit YouTube URL are pushed to every healthy configured account without title guessing or duplicate insertion. Local categories and completion state remain authoritative, one failed account does not block the others, removals are never mirrored, exact-title collisions are left unresolved, and OAuth tokens and playlist IDs stay out of git. An explicit broader OAuth opt-in adds narrowly implemented exact-name private playlist lookup/creation and playlist-item insertion; Clockwork exposes no YouTube update or delete operation. Each list page also links to its private playlists with a ▶ Playlist button per account.
- Clockwork can federate its Tradility menu route to Tradility's own standalone web application with `CLOCKWORK_TRADILITY_WEB_URL`. With no URL configured, the existing in-process page remains as a migration fallback; Clockwork no longer needs to be the permanent owner of Tradility's domain UI.
- Tradility shows a broker order in Position and orders as soon as its confirmation succeeds, marked "awaiting broker sync" and de-duplicated by its broker order ID, instead of discarding the confirmation response and immediately replacing the view with the older scheduled order export. Indicator buttons now wrap within the controls when their weak/strong tags make the row wider, preventing horizontal page overflow on phones.
- Tradility Accounts panel lists bank accounts (checking, savings, money market) from the finance pipeline's bank statements: balance, statement date, and that period's money in, money out and interest, with a flag once a statement is over 45 days old, and their total in the panel heading. They are kept apart from the investment totals and order previews.
- Tradility indicator settings. A Settings panel next to the indicator buttons chooses what each indicator measures, kept in this browser: RSI period (7, 14, 21) and its oversold and overbought bands; VWAP window (10, 20, 50) and how far from VWAP counts as decisive; the Trend pair of averages (20/50 up to 200/300) and its decisive gap; and which groups of fundamental rules count (valuation, profitability, growth, balance sheet, short interest) and how many must agree. The buttons, legend, card labels and badges follow the chosen windows, and the weak/normal/strong strength still weights each one. Needs the `indicator_variants` windows the analysis now writes (Tradility #46).
- Auto Pass web example (`examples/auto-pass/web-service.toml`, loopback port 8081) and an Infrastructure card with a key icon for the `auto-pass-web` registry entry on Home.
- Tradility: indicators have a strength. Each indicator button (RSI, VWAP, Trend, Fundamentals) cycles off → weak → normal → strong → off (the last one on skips off) and shows it as a tag. Weak counts half in the joint buy probability; strong counts double and speaks only on a decisive reading (RSI at or past 30/70, price 3% from VWAP, a 5% gap between the 50- and 200-day averages, or fundamentals with at least half the rules agreeing); normal is unchanged. Cards list each indicator's strength beside its read.
- Tradility: a Fundamentals · Schwab card in each company's detail (the reported figures and the buy/neutral/sell rule behind each, with its threshold on hover), and a Fundamentals indicator button that joins RSI, VWAP and trend in the card colouring and the joint buy probability. Funds and ETFs show figures without rules.
- Tradility on a phone, the real cause: each stack of detail cards sized itself to its widest card (the orders table and order form), so everything above Technical market rating was wider than the screen and ran off it. The stack's column is now `minmax(0, 1fr)` and cards can shrink; the page no longer clips what overflows. Measured at 390px wide: a card was 527px before and is 360px now.
- Tradility on a phone: opening a card no longer zooms the page. The detail column could be stretched past the screen by the wide orders tables (the browser then zoomed out the layout), and form fields under 16px made iOS zoom in on focus. On narrow screens the column is `minmax(0, 1fr)`, tables scroll inside themselves, fields are 16px, buttons are taller to tap, and text size adjustment is pinned.

- A Fidelity ticket has a **Stage on Fidelity** button when the trading policy allows driving Fidelity's website (`[fidelity] automation`): the service fills in Fidelity's own ticket in its browser, presses Fidelity's Preview and checks what it shows (symbol, action, quantity, price, type, duration, account) against the order, listing each check. In `stage` mode that is all; in `submit` mode a **Place order at Fidelity** button then presses the final button once, after the usual confirmation (button or typed phrase). Routes `POST /api/tradility-trade/fidelity-stage` and `fidelity-submit`.
- Fidelity accounts are in the order form, assisted. Fidelity has no order API for individuals, so choosing a Fidelity account changes Preview into **Prepare Fidelity ticket**: the order is checked against your trading policy and Fidelity's own rules and laid out in Fidelity's fields (with the position impact from the latest statement and its date), with a Copy ticket button and a link to Fidelity's trade page. Nothing is sent from here: you submit it on Fidelity, then press "I placed it at Fidelity" to record it as an open order so it shows in your orders, impact and targets. `POST /api/tradility-trade/fidelity-ticket` forwards to the trader service.
- Fix an average entry that was far too low. A position held in several accounts divided the cost of the accounts that report one by every share, so a lot with no cost on record (a Schwab line in a statement) dragged the average down: VWO showed $36.16 instead of about $48.75. The average is now the cost of the lots whose cost is known over the shares in those lots, and when the trader service is running Schwab's own live quantity and average price replace the statement lot, which completes it. If some shares still have no cost on record the position says how many shares the average covers and the chart labels the line "(partial)".
- The Tradility stock detail view now fills the window below the top bar instead of a centred 980px card that covered it, so the bar and its theme, font and size controls stay available and keep the detail in step with the theme. The chart grows with the window. A new ⧉ button opens the detail in its own resizable browser window (the same page in a detail-only mode with the top bar, `?ticker=X&window=1`); the theme follows because windows of the site share it, and closing it closes the window. Also fixes the share bars, whose track needed `display: block` to have a height.
- Bars for the numbers. A horizontal share bar shows what you hold against the To Invest target: the filled bar is what you hold now, a lighter segment is the change an order makes (hatched when it shrinks the holding) and an ink tick marks the target, so a bar with a target reads as progress to it. They appear on each stock card, large on the target card (including the effect of all open orders), in the "If this fills" column for each open order, under the order preview's target line and in the share columns of the preview and the where-you-hold tables. They use theme colours only, carry the exact figures in a tooltip and an `aria-label`, and the text and tables remain.
- Each open order in the Position and orders card now shows what it would do to the holding: an "If this fills" column gives the share of the portfolio it would leave you at and how far that is below or above your To Invest target (for example "→ 0.52% · 0.88% below target 1.40%"), and a line under the table gives the result if every open order fills. It uses the same base as To Invest, so the numbers match the cards.
- Tradility is integrated with To Invest. Each stock card shows the ticker's target allocation next to what you hold now ("Target 0.70% · now 0.30%", blue when below target, red when above); a new sort puts the furthest-below-target holdings first. The detail view has a To Invest target card with the category, the target (and, for a basket category, its share of the basket), the current allocation, how far below or above you are and roughly what that is in dollars, with a box to set or clear the target or, for a ticker not on the list, to add it to a category. The order preview adds a line saying where the order moves the holding against its target. Backed by `GET /api/tradility-targets` and `POST /api/tradility-targets/<ticker>`.
- Fix the account list showing "$0 to trade": it now shows what each account can spend on new stock orders (Schwab's projected stock buying power) and the warning before a preview compares against that. The preview and the Position and orders card now show where a stock is held, account by account, with shares, value and share of each account (the chosen account before and after the order, plus any statement-only institution such as Fidelity with its date).
- The order form is clearer about money. The position impact names its rows: this account, all live accounts (Schwab and Binance.US, read now) and, when the holdings export has them, institutions known only from statements such as Fidelity, with the statement date. The Accounts panel lists those statement-only institutions with an "as of" date and the header total separates live from statement values. The account list shows what each account can spend, and a buy that costs more than the chosen account has available gets a warning before you preview it.
- The order preview shows how the order changes your position: shares, dollar value and share of the account and of everything you own (all Schwab accounts plus Binance.US), before and after, with the cash and buying power left. When the trading policy sets `confirmation = "button"` the preview has a Place order button that ships the previewed order instead of a typed phrase (the preview is still single-use and expires in two minutes); cancellations always keep their typed phrase.
- The generated cron commands in `bug-sweep-agentic`, `ci-repair-agentic` and `template-consolidation-agentic` used systemd's `%h`, but a `%` in a crontab line means newline, so the job would have run a truncated command. They use `$HOME`, and a test now fails if any example's cron command contains an unescaped `%`.
- The Tradility order form has template buttons: buy 1, 2, 4 and 8% under the live mark and sell 1, 2, 4 and 8% over it (or the ones your trading policy defines under `[[templates]]`, optionally with a duration and hours). A template sets the side, a limit order and the price from the mark and focuses the quantity; it never sends anything, so you still preview and type the confirmation.
- Manifests can now reproduce a unit that runs several commands or carries log metadata: `exec_start_then` (further `ExecStart=` lines for a oneshot service, refused by launchd and Task Scheduler until their override sets one command), `documentation` (`Documentation=` on the service and timer) and `syslog_identifier` (`SyslogIdentifier=`). `session-control-prune` now renders its two commands as the installed unit has them, not as one `sh -c`.
- Register the remaining public jobs that were installed but not listed: `github-ci-email-monitor`, `job-guard-notify`, `portfolio-purge`, `repository-description-sync-weekly`, `crew-chief-benchmark-weekly`, `intake-newsletter`, `session-control-prune`, `tachometer-bench`, `tachometer-host-sampler` and `weekly-blog-agentic`, so the Schedule page lists them. Each manifest was built from the installed unit; on a machine without `~/git`, keep a `*.local.toml` twin with the real paths, because Enable reinstalls the unit from the manifest. Their generated cron commands use `$HOME`, not `%h` (a `%` in a crontab line means newline); the older siblings still carry `%h`.
- The Tradility order form covers every Schwab duration (Day, GTC, Fill or kill, Immediate or cancel, End of week, End of month, Next end of month) and session (Regular, Extended, Pre-market only, After-hours only), so DAY, DAY EXT, GTC and GTC EXT are all there. Choices that Schwab would refuse are greyed out as you pick: a market order is regular hours and day, fill or kill or immediate or cancel; extended, pre-market and after-hours orders are limit orders that are day or GTC. The preview and confirmation show the duration and hours.
- The Tradility order form has Duration (GTC or Day) and Trading hours (Regular or Extended) for Schwab orders, starting from the policy's `[defaults] time_in_force` and `session`. Extended hours (Schwab's pre-market-through-after-hours session) take limit orders only, so choosing a market order switches back to regular hours. The preview shows both.
- Register traction-control's daily GitHub Actions check (`actions-switch-check`) as `examples/traction-control/actions-switch-check.toml`, so it appears in the Schedule page and can be enabled or disabled there. Like its siblings it carries `after=`, so it renders for systemd and cron but not launchd.
- The Accounts panel lists deposits, withdrawals and transfers over the past year (Schwab cash movements and Binance.US deposits and withdrawals, internal journals hidden unless you tick the box) with totals in and out, read through the trader service (`GET /api/tradility-transfers`). When Binance.US withdrawals are enabled in the trading policy the panel also offers a withdrawal form: choose one of the policy's destinations, enter an amount, preview, and type the exact phrase to send. Addresses are never typed in the page and are shown only by their last six characters. Schwab transfers are history only (its API has no money movement).
- A crypto ticker's order card says why there is no order form when Binance.US trading is not enabled (it needs a separate trade-enabled key and `binance = true` in the trading policy) instead of failing at preview.
- Tradility shows your account details. An Accounts panel above the grid lists each Schwab account (margin or cash, margin eligible, total value, cash, cash available to trade, buying power, available funds, day-trading buying power, margin balance, equity, positions, and flags for pattern day trader, closing only and margin call) and the Binance.US spot account valued in dollars, with a total across them, loaded live through the trader service (`GET /api/tradility-accounts`). A Binance.US API key that can withdraw is flagged. The order form shows the selected account's buying power and cash.
- Tradility order form defaults: a good-till-cancelled limit order 2.5% below the live mark for a buy and 2.5% above for a sell (the policy's `[defaults]`), refilled when you switch side or type and editable before you preview. Schwab orders must name one of the policy's allowed accounts, chosen from a list with no default; the preview and confirmation show the account. An allowlist of `*` is honoured. `GET /api/tradility-trade/quote/<ticker>` forwards the mark.
- Tradility can place orders from the detail view, through Tradility's local trader service (`CLOCKWORK_TRADILITY_TRADER_URL`, default `http://127.0.0.1:8768`), never with credentials of its own. The Position and orders card shows a form only when the service is running, trading is enabled in its policy and the ticker is allowlisted: enter a limit or market order, Preview (policy checks plus the broker's own validation, nothing placed), then type the exact phrase (for example `BUY AAPL`) to send it. Open broker orders get a Cancel at broker button that needs `CANCEL <order id>`. Routes: `GET /api/tradility-trade/status`, `POST /api/tradility-trade/{preview,confirm,cancel}`.
- The Tradility Position and orders card also lists the brokers' own orders (Schwab, Binance.US), read from Tradility's read-only export (`CLOCKWORK_TRADILITY_ORDERS_EXPORT`, default the sibling tradility checkout's `exports/tradility-orders.json`). They sit with your hand-entered orders, marked by source with a note on what was synced and when; they cannot be edited here and never feed the fallback average entry. Open broker orders are drawn as dashed lines on the chart.
- Tradility records your position and orders per ticker. The detail view has a Position and orders card: quantity, average entry (total cost basis over quantity from the holdings export, summed across accounts and only when every lot has a cost basis; otherwise the average of your recorded filled buys), and the gain against the last price, plus order history and open orders you add, mark filled, cancel or delete (`/api/tradility-orders/<ticker>`, stored owner-only in the gitignored `config/tradility-orders.json`, `CLOCKWORK_TRADILITY_ORDERS_FILE` moves it). The chart draws the average entry as a horizontal line and each open order as a dashed line; the price scale widens to include them. No broker export of orders exists yet, so orders are entered by hand.
- The Tradility detail chart range (1M to 5Y) is shared by every card: changing it on one chart applies to all of them, including when you step with previous and next, and the choice is remembered in this browser.
- The Tradility detail view links to the symbol's Yahoo Finance quote page as well as the company website. Both sit in one row under the company overview, and Yahoo shows for every symbol, including ones with no profile yet.
- The Tradility detail view has previous and next buttons (and the left and right arrow keys) that step through the cards in the order the grid is showing, with a position count; the buttons stop at the ends, and a slow load no longer overwrites the card you stepped on to.
- Tradility indicators are now selectable together. RSI 14, VWAP 20 and Trend 50/200 are toggles (at least one stays on); with several selected each card shows a joint buy probability (each indicator's read combined as independent evidence on an even prior, P = Πp / (Πp + Π(1−p)), each held off 0 and 1), the per-indicator reads, and how many signals were available. The colour, buy/sell/neutral counts, badge, sort and legend all follow the joint value. One indicator behaves as before.
- Remove the ← button from the Tradility header: it duplicated Home in the top bar and led to the Clockwork jobs page.
- Re-vendor the portfolio theme files: the top bar's theme menu has This site / Everywhere and "Follow the global theme" (the global theme is set through traction-control's theme writer; without it the choice stays per site).
- The Tradility page replaces the ambiguous Reload and Run refresh controls
  with one **Update market data** action. It starts or follows the systemd job,
  shows elapsed progress, polls a lightweight artifact-version endpoint, and
  automatically loads the completed analysis; startup, no-change, provider,
  and 15-minute timeout failures remain visible without discarding current
  cards.
- Tradility ticker details now include an on-demand company/fund overview with
  canonical name, sourced description, sector/industry or fund classification,
  headquarters, website, exchange, employees, entity type, and profile
  freshness. Missing official mission/vision text is labeled as unavailable,
  never generated from the description; verbose profiles stay out of the card
  collection response.
- The Themes page registers a placeholder Home card for a page that is not built yet. The card is stored in the gitignored `config/placeholders.json`, shown on Home as planned, and listed under Names and icons. It stays out of the services menu and the published `nav.json`. Registering it adds a To Code item for the repository group and repository you name.
- Clockwork no longer has its own top-bar icon: it is the first entry of the Services menu (⏰ Clockwork, the jobs page), and the published `nav.json` drops the top-level item. A name or icon you set for the old top-bar item (`nav:clockwork`) no longer applies; rename the Services entry (`/`) instead.
- The top bar has a font rotator and a size rotator, each like the theme rotator: the button steps to the next choice, and the menu beside it lists that choice only.
- Themes page: colours are named by what they are for (Success, Error, Warning, Highlight, Accent, Page and panels, Text) and grouped, each with a one-line note on where it is used. Every icon and colour opens a picker over the shared library (search, your own first, or type one), and both libraries can be browsed and added to your own. A new theme is made with an inline form (title plus a start-from theme) that derives a valid internal name, instead of a browser prompt; save and reset errors now show as "Could not save: ..." in a bar that stays in view.
- The shared size is a page scale: tiny, small, medium, or large (80%, 90%, 100%, 125%). It zooms the whole page, so text that sets its own pixel size scales with the headings. Medium is the current page. The Aa control and the Themes page use these names.
- Pages follow the shared font and base size (`var(--font)`, `var(--font-size)`, `var(--mono)`). The top bar's Aa control changes the font and size for this browser only. The Themes page edits the shared default (system, serif or mono, and 14, 16, 18 or 20).
- The Themes page also renames and re-icons things: a "Names and icons" section lists the top-bar items, the Lists, Media and Services menu entries and every Home card (including registry services) with their defaults, filterable, with a Default button per row and your saved emoji as suggestions. Overrides are stored in the same owner-only layer (traction-control `[labels]`, rendered to `labels.json`) and applied to the server-rendered top bar, the Home cards, the Services menu and the published `nav.json`, so other services' top bars follow. A page is identified by its path and a registry service by its name. The top-bar menus now come from one source: the template loops over `site_chrome.site_menus()` instead of repeating the entries by hand (the rendered bar is unchanged without overrides).
- List Themes in the Services menu, beside Schedule, as well as on the Home card. The Clockwork group is left out of that menu, so a Home card alone does not appear there.
- Add a Themes page (`/themes`, a Home card) for your own portfolio themes: rename and recolour any theme with a live preview, create themes of your own from a tracked one, choose which are offered and in what order, and keep named colour and emoji libraries to pick from. It edits an owner-only layer (`~/.config/traction-control/themes.local.toml`) through traction-control's `portfolio_themes.py`, which does the validation and renders the merged themes once; Clockwork never edits another repository's files. Clockwork's own pages load the rendered themes (`/theme-assets/theme.css`, `theme.js`, `chrome.js`, falling back to the vendored copies), and the Caddy-served `/srv/portfolio-chrome` gets a copy of them (Caddy cannot follow a link into an owner-only home). Set `CLOCKWORK_TRACTION_CONTROL_DIR` if traction-control is not the sibling checkout.
- Tradility chart labels format date-only market points in UTC so local time
  zones cannot shift them to the prior day, and range summaries use the
  producer's calendar-period return when available.
- Tradility stock cards now open a responsive historical-performance dialog
  with range-selectable price charts, period returns, 52-week range, technical
  rating components, indicators, and data-source provenance. The list API omits
  heavy chart history and a ticker-detail endpoint serves it on demand.
- To Watch keeps the torrent search button on items already marked watched, so a title can be found and downloaded after watching it elsewhere.
- To Code items now carry an entry date and a staleness rank. New items are stamped with `added`; older ones are dated at render time from the history log's `add` event or a `[manual:YYYY-MM-DD]` title prefix, without rewriting the backlog. Each item shows its age (stale after 30 days), the filter bar counts stale open items, and a Sort control orders each repository by priority (default), newest first or oldest first, with undated items last.
- To Code auto-sorting now classifies only the repository and takes the group from it. Checked on the real
  backlog (27 repositories, leave-one-out), no repository spans two groups, and asking a model for the group
  was right about 46% of the time against 73% for always answering the commonest group. It also uses one
  model for the one question (two different models made Ollama reload each on every add, 30 seconds),
  merges repository names that differ only in case before asking, offers only the given group's repositories
  when a group is given, and waits up to 60 seconds for a model that has to load. Settings:
  `CLOCKWORK_TRIAGE_MODEL` (default `clef`) replaces the two per-question models; the threshold stays 0.6.

- Treat systemd's `activating` state as an in-progress Tradility refresh even
  though `systemctl is-active` exits nonzero for a running oneshot service.

- To Code: the group and the repository are now optional. A blank one is filled in by a System One decision
  model (`web/code_triage.py`, `POST /to-code/add-item` and `POST /api/code/add-item`): `clef-flash` picks the
  repository (about 190 ms) and `clef` the group, each from a list of the allowed values with a description
  (repository descriptions come from each checkout's `.github/repository.toml`) plus `unknown`. Anything the
  model is unsure about (confidence under 0.6, `CLOCKWORK_TRIAGE_THRESHOLD`) or cannot be asked is filed as
  `unknown`, in an `unknown` group and repository, never guessed. A group or repo you give is never changed. The
  API reports the outcome as `auto_sorted`. Turn it off with `CLOCKWORK_TRIAGE=0`; the endpoint and models are
  `CLOCKWORK_TRIAGE_URL`, `CLOCKWORK_TRIAGE_GROUP_MODEL` and `CLOCKWORK_TRIAGE_REPO_MODEL`. The defaults come from
  crew-chief's synthetic `backlog-triage` benchmark; the group is the hard part (see its results).

- Log what is watched in the library viewer and show it on To Watch. The player reports a video once 90%
  of it has played (`POST /library/play/<path>/watched`, CSRF-protected); Clockwork appends it to
  `config/watch-log.jsonl` (gitignored personal data; `CLOCKWORK_WATCH_LOG_FILE` moves it) with the title,
  season and episode, ignoring clips and repeats within three hours. The To Watch page links the log to
  list items by title and shows a badge such as `▶ 3 episodes · last S01E03 · 2 days ago`.

- Add a weekday post-market user-systemd manifest for Tradility, report
  analysis age/staleness through its API and page, and make dashboard refresh
  start the non-blocking analysis service instead of merely rereading stale
  JSON. A separate Reload control remains available.

- Add the missing **Tradility** card to the Home page, and a test that every page in the top bar's Lists and Media menus has a Home card.

- Also publish the navigation into traction-control's portfolio site store (`~/.config/traction-control/sites/_shared`,
  `PORTFOLIO_STORE` to move it) when it exists, so pages served without a Caddy in front still get the top bar's menus.

- Refresh the vendored portfolio theme: adds the Hulk (purple and green) and Neon themes.

- Add the High contrast white and Liberty themes, rename High contrast to High contrast dark, and
  give the top bar's theme control a drop-down menu of every theme beside the rotator button. The
  bar shows the theme's stripe under it (Liberty has a red, white and blue one). The vendored theme
  and top bar files are refreshed from traction-control.

- Also publish `theme.css` and `theme.js` into the shared directory, so services can use the
  shared colour tokens and apply the saved theme before first paint.
- Refresh the vendored top bar script: it now isolates itself from host-page CSS and sits outside the page body.

- Publish the top bar for other services: Clockwork writes `nav.json` (Home, Clockwork,
  Lists, Media, Services from the wiring-harness registry) and `chrome.js` into the shared
  directory the Caddy serves at `/_portfolio/` (`CLOCKWORK_CHROME_DIR`, default
  `/srv/portfolio-chrome`), refreshed every five minutes while the web service runs.
  `GET /api/site-nav` returns the same document. Services load the bar with
  `<script src="/_portfolio/chrome.js" defer></script>`.

- Adopt the portfolio system themes on every page (Midnight, Daylight, Ember, High
  contrast). Pages use the shared colour tokens instead of per-page palettes, and the
  top bar has a rotator button that steps to the next theme on each click; the choice
  is saved in the browser and follows the system light/dark setting until chosen.
  Grocery and shopping category badges, the Tradility heat map and the nav menus
  adapt to the active theme. The theme files in `web/static/` are vendored from
  traction-control (`docs/system-theme.md`).

- Link **Machine load** from the Home page's Clockwork group.
- Add a **Schedule** page (`/schedule`, JSON at `/api/schedule`): all user and
  system timers in one list ordered by next run, with time until it fires, the
  last run and the last outcome of each job, plus a Home badge and nav link.
- Add a **Machine load** page (`/ecu`, JSON at `/api/ecu`) showing the held lease
  (command, priority, mode, GPU size), GPU memory and processes, and each
  timer job's run/defer verdict from `ecu status --json`. It degrades to an
  explanation when the `ecu` command is missing (`CLOCKWORK_ECU_BIN` overrides
  the lookup).
- Refer to the engine control unit generically in the upscaler README and
  `upscale.env.example` instead of naming its private repository.

- Stop naming a private repository in the To Code source: the add form's extra
  repository suggestions come from `CLOCKWORK_CODE_EXTRA_REPOS` (comma-separated,
  empty by default), and the placeholder and tests use synthetic names.

- Add `start_condition` and `time_limit` job fields, rendered as systemd
  `ExecCondition=` and `TimeoutStartSec=`/`RuntimeMaxSec=`, so a start guard and
  run limit travel with the rendered unit. launchd and Windows refuse them until
  a per-target override clears them.

- Add a P1-P4 priority rating and "in person", "needs a human" and "needs sudo"
  flags to To Code items. Items sort by priority within each repository, can be
  filtered by priority or flag, and are editable through `/to-code/update-item`
  and `/api/code/update-item`. A "Ready for an agent" filter shows items with
  none of the flags.

- Keep the To Code filters across the reload after any row action, and have its
  add form follow the active repo-group and repository filter, narrowing the
  repository suggestions.

- Add a To Code portfolio backlog list grouped by repository family and
  repository, with explicit family selection, local history, and a Home link.
- Cancel a waiting per-render ECU lease when an external pause request arrives,
  allowing the queue coordinator to acknowledge without waiting for admission.
- Wake the upscaler queue for ECU pause requests even while idle, throttled,
  or waiting for disk space, so a waiting lease receives acknowledgment.

- Cooperatively yield the AI upscaler to ECU leases after saving its current
  chunk, release the render subprocess's GPU memory, then resume when the lease
  ends.

- Pin registry services to the `/home` top bar with `top_bar = true` in their
  wiring-harness registry entry; an entry may also set its own `icon`. The
  link URL comes from the registry, and pins stay in the ignored local
  registry instead of this repository.

- Render stepped-minute calendars such as `*:0/5` for `launchd-user` (one
  `Minute` entry per step, no hour) and `windows-user` (a daily trigger that
  repeats every step). A five-minute sampler previously could not be
  installed on macOS at all.
- Add the shared `install-check` workflow to CI, so the package is installed
  from a clean runner on Linux, macOS and Windows and the `clockwork` console
  script is actually executed. `python-ci` imports the source tree on ubuntu
  only, which cannot catch an entry point that fails to resolve once installed.
  Pinned to Python 3.10 and 3.14 — the floor pulls the `tomli` dependency
  marker and the ceiling must not.
- Add a native `windows-user` target rendering Windows Task Scheduler XML, so
  the three desktop platforms are all first-class. Paths are judged with
  `ntpath` rather than the rendering host's rules, `%h` becomes `%USERPROFILE%`
  for Task Scheduler to expand at run time, and files are written as UTF-16
  because `schtasks /Create /XML` rejects UTF-8. `randomized_delay_sec` maps to
  `RandomDelay`, which launchd cannot express.
- Add `[jobs.windows]` overrides, the same mechanism as `[jobs.launchd]`.
- `--target` now detects Windows as well as macOS and Linux.

- Add `[jobs.launchd]` per-target overrides so one manifest can carry
  systemd-only settings and still install on macOS. Keys there replace the
  job's values when rendering `launchd-user` only; empty values clear a field.
  Previously a job declaring `after` or `randomized_delay_sec` could not render
  for launchd at all, so the settings had to be deleted for every platform.
- `--target` now defaults to the scheduler detected from the platform
  (`launchd-user` on macOS, `systemd-user` on Linux, `windows-user` on Windows)
  instead of being required. Platforms with no native target refuse to pick one
  and say so rather than falling back to systemd and writing units nothing will
  read. An explicit `--target` still works everywhere, which is how any host
  renders `cron` or systemd units for a container.
- Initial `clockwork` scaffold for declarative cron and systemd rendering.
