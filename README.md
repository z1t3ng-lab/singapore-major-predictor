# Major Forecast — PGL CS2 Major Singapore 2026

A runnable, local-first tournament predictor for the Singapore Major. It combines a FastAPI prediction and simulation service, a Next.js interface, and a persistent SQLite database. By default, the event structure is present but the participant list is empty; synthetic teams load only after the user selects the demo option.

## Quick start

Requirements: Python 3.11+ and Node.js 20+.

```bash
# Terminal 1 — API and SQLite database
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
uvicorn app.main:app --reload --app-dir backend

# Terminal 2 — web interface
npm install
cp .env.example .env.local
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The API is at [http://localhost:8000](http://localhost:8000), with interactive documentation at [http://localhost:8000/docs](http://localhost:8000/docs).

For a production-mode local run, build once with `npm run build` and start with `npm run start`. This is also a fallback if the development server cannot access the machine's file-watching service.

The API creates `backend/data/major.sqlite3` on first startup with the event and stage configuration, but no team identities. On the Method & Data page, either import the official entrant file when it is available or select **Load synthetic demo tournament** to explore the simulator. The `/api/demo` endpoint only loads into an empty dataset. A prior automatically seeded demo-only database is migrated back to the empty participant state; an explicitly loaded demo remains in place across restarts. To use another database location, set `MAJOR_DB_PATH` before starting the API. Copy `.env.example` to `.env.local` to change the browser-facing API URL.

## Desktop app (macOS)

Double-click **Major Forecast** in Applications or its Desktop shortcut. It opens the full app in its own window and manages the web and Python services automatically.

### Standalone installer

The bundled edition includes Node, Python 3.12, the API, dependencies, and the built interface. It works without the source folder, a virtual environment, or installed development tools. Build on a Mac matching the target architecture, after installing the Quick start dependencies and Apple Command Line Tools:

```bash
npm run desktop:package
npm run desktop:install
```

The package command runs an isolated bundled-runtime smoke check before creating the disk image. It produces `desktop/dist/Major Forecast.app` and `desktop/dist/Major-Forecast-1.2.0-macOS-arm64.dmg` on Apple Silicon (the architecture appears in the filename). Open the disk image and drag the app to Applications. The package builder currently requires the official Python.org **3.12 framework** and a self-contained Node distribution; it validates and relocates native library dependencies. Internet access may be needed to obtain the matching Node redistribution license. Third-party package licenses are retained in the bundle.

Bundled app data lives in `~/Library/Application Support/Major Forecast/major.sqlite3`. The `desktop:install` command copies the checkout's existing database there **only on the first bundled installation**, using SQLite backup; later upgrades keep the existing app database. A drag-and-drop installation on another Mac starts with an empty participant list. The browser development server keeps using `backend/data/major.sqlite3` unless `MAJOR_DB_PATH` is set to the desktop data path. The two editions do not automatically synchronize separate databases.

The app is locally signed and **not Apple notarized**. Distribution to other Macs requires a developer to sign/notarize it with Apple credentials; macOS may otherwise block a downloaded copy. The disk image is generated locally and excluded from Git. **Check GitHub releases** checks the repository's published releases; it does not silently install executable code. If no release is published, it says so.

### Developer launcher

For an app that stays connected to this checkout, use `npm run desktop:build` followed by `npm run desktop:install`. This lighter edition uses the checkout's database and needs its `.venv`, `node_modules`, and installed runtimes. It rebuilds its separate `.next-desktop` interface after source changes. Packaging the standalone edition again replaces this build output with the bundled app.

Both editions use a random loopback port and a private Unix socket for the API. Cross-site requests and unexpected Host headers are rejected by the desktop proxy. Closing the window stops the services. File pickers, save dialogs, copy/paste, reload, zoom, **File → Show Saved Data**, and **Help → Show Startup Log** work in the native window. The bundled edition stores logs alongside its database; the developer edition uses `.local-run/`. The installer preserves the previous app for rollback.

### Docker Compose (optional)

```bash
docker compose up --build
```

The database is stored in the `major-data` volume. `docker compose down -v` removes that database and its imported records.

## What's implemented

- **Tournament views:** overview, Stage 1, Stage 2, Stage 3, eight-team playoffs, teams, and data/methodology. When no complete field is loaded, the stage views show unfilled slots without inventing teams or predictions.
- **Swiss engine:** round-based pairing by record, configurable 3-win / 3-loss thresholds, best-of-one opening matches and best-of-three progression/elimination matches, Stage 3 best-of-three series, standings, advancement/elimination, and rematch avoidance when a complete no-rematch pairing is possible.
- **Progression:** 16 teams begin Stage 1; eight direct entrants join the Stage 1 advancers in Stage 2; eight more direct entrants join Stage 2 advancers in Stage 3; its eight advancers enter the playoffs.
- **Playoffs:** four quarterfinals, two semifinals, a best-of-five final, one projected champion, and configurable provisional seed order.
- **Predictions:** Elo expected-score probabilities plus cutoff-safe imported match context, then best-of series probabilities under the stated identical/independent map assumption. Each matchup reports which signals were used, their sample sizes, and fixed log-odds adjustments.
- **Monte Carlo:** full tournament simulations estimate reaching Stage 2, Stage 3, playoffs, semifinals, final, and championship. The seed is reproducible; each estimate includes a standard error.
- **Scenarios:** choose different winners for unresolved projected matches and recompute dependent pairings. Overrides remain separate from imported completed results.
- **Set match result:** record, edit, or clear a winner and optional validated series score from each known matchup. The UI keeps the original forecast and probabilities separate from the confirmed result, shows whether the pick was correct, and recalculates records, Swiss progression, playoff slots, and tournament odds.
- **Result dependencies:** every confirmed winner is applied to the projected path and every Monte Carlo run. Editing an earlier result rebuilds records from the match list; if later confirmed generated matches no longer fit, the app lists them and requires explicit confirmation before invalidating them. Unaffected results remain locked.
- **Model updates:** result entry recalculates current Elo/history inputs when their play and availability timestamps qualify, while reusing the last run's trained coefficients (or its baseline) instead of fitting the statistical model again. A later explicit simulation can train using the now-available history. Manual result entry never fills in map or player statistics.
- **Persistent imports:** CSV teams and matches; JSON bundles can also include players, time-dependent roster memberships, map results, and time-stamped player statistics. Imported event results remain fixed in every run.
- **Exports:** save the projected tournament as CSV or the complete prediction run as JSON.
- **Model training:** when at least 200 eligible historical matches are available and scikit-learn can train a chronological 80/20 split, a logistic regression on pre-match Elo difference and series format becomes active. Its holdout reports log loss, Brier score, accuracy, and five-bin calibration error. Those metrics explicitly evaluate the Elo-and-format model before the separate context priors. Below the threshold, the app reports and uses the Elo baseline plus any available context priors. No score is fabricated.
- **Context features:** map results are shrunk toward 50% with a Beta(4,4) prior. The map-rate difference is multiplied by 0.70 and capped at ±0.24 log-odds. Veto preference reweights maps using `exp(0.7 × (team A preference + team B preference))`, where each preference is `(picks − bans) / (actions + 4)`, with weights clipped to 0.5–2. Active player rating difference is multiplied by 0.60 and capped at ±0.20; ADR difference is multiplied by 0.002 and capped at ±0.10. Both use only a shared metric version and at least three active players per team. Dated roster continuity uses the share of active players with 90+ days together; the difference is multiplied by 0.10 and capped at ±0.10. Head-to-head uses a 180-day half-life, a six-match shrinkage prior, and roster-overlap relevance when historical rosters exist; its coefficient is 0.15 and cap ±0.15. The sum of context adjustments is capped at ±0.45. These are transparent heuristics, not learned effects. Missing, stale, future-dated, or incomparable inputs are skipped and surfaced.
- **Data model:** SQLite tables cover teams, players, roster memberships, events, stages, stage entrants, matches, map results, map vetoes, player statistics, prediction runs, source logs, and schema migration versions.

The main backend code is split into `app/engine.py` (match probabilities, Swiss and playoffs), `app/feature_engineering.py` (cutoff-safe roster, map, veto, player and H2H features), `app/model.py` (cutoff-aware Elo and optional training), `app/providers.py` (replaceable source boundary), `app/database.py` (SQLite), and `app/main.py` (API and imports). The current schema is in `backend/data/schema.sql`; migrations 002–005 add map veto history, roster availability timestamps, and persisted match results, versioned prediction snapshots, and named scenarios.

## Tournament facts and configuration status

The official event name used here is **PGL CS2 Major Singapore 2026**. PGL's event page states November 25–December 13, 2026, 32 teams, three competitive stages, and a best-of-five grand final at Singapore Indoor Stadium. [PGL event information](https://www.pglesports.com/cs2/pgl-major-singapore-2026/)

As checked on October 19, 2026, the organizer page had not published the participant list or the detailed Swiss pairing rulebook. HLTV's invite tracker showed unfilled VRS invite slots and identified November 2, 2026 as the VRS cutoff for determining invites. This app therefore leaves team records blank by default; it does not substitute a different event or present predicted invitees as confirmed teams. [HLTV invite tracker](https://www.hltv.org/major/invites) · [HLTV coverage hub](https://www.hltv.org/major/) · [PGL event page](https://www.pglesports.com/cs2/pgl-major-singapore-2026/)

HLTV's event tracker lists Stage 1 on November 24–27, Stage 2 on November 28–December 1, Stage 3 on December 2–6, and playoffs on December 10–13. Stage 1 begins one day before PGL's published November 25 event window, so the app flags this date difference. [Stage 1](https://www.hltv.org/events/9303/pgl-major-singapore-2026-stage-1) · [Stage 2](https://www.hltv.org/events/9304/pgl-major-singapore-2026-stage-2)

HLTV's coverage pages describe three 16-team Swiss stages, eight direct entrants at each of Stages 2 and 3, eight teams advancing from each stage, BO1 openers / BO3 progression and elimination series in Stages 1–2, BO3 matches in Stage 3, BO3 quarterfinals and semifinals, and a BO5 grand final. The exact opener, same-record pairing, rematch handling, tiebreakers, final seeds, and playoff bracket order remain configured assumptions until the organizer publishes those details. The current 3-win / 3-loss cutoffs match the tracker structure but are explicitly labeled provisional.

The 32 invented names and Elo inputs in `backend/data/demo_seed.json` are retained solely as optional synthetic demonstration records. They have no real player rosters or historical results and are never shown by default. The UI labels them as synthetic when the demo is loaded. Once the November 2 cutoff has passed and the invite list is published, replace the demo with the confirmed team records and direct-stage assignments.

## Data collection and limitations

HLTV is the intended source for user-provided exports, but this build makes **no automated HLTV requests**. HLTV's [terms](https://www.hltv.org/terms) prohibit data mining and web scraping. No official API is assumed or claimed. The replaceable `HltvProvider` is disabled and does not contact HLTV. Import data you are authorized to use through the JSON bundle; this supports historical matches, per-map results, map veto picks/bans, players, metric versions, player statistics and dated roster membership.

All imports are recorded as user-supplied unless the row includes another source label. Include `source_url`, `available_at`, `played_at`, metric version, and measurement period when available. A result, map result, veto, player statistic, and roster membership is used only if its source record and underlying match were available by the selected cutoff. Conflicting active team memberships, unknown metric versions, fewer than three comparable player values, and insufficient roster dates are excluded rather than guessed.

## Data formats

Templates are available in `templates/` and from the UI:

- `teams.csv`: `id,name,region,seed,elo_rating,rating_source,stage_entry`
- `matches.csv`: stable match ID, teams, format, status, winner/score, play and availability times, source and URL
- `import.json`: arrays for `teams`, `players`, `roster_memberships`, `matches`, `map_results`, `map_vetoes`, and `player_stats`

Each `map_vetoes` item needs `match_id`, `sequence_no`, `action` (`pick` or `ban`), `map_name`, and the acting `team_id`; `available_at` and `source` are recommended. Match records use stable team IDs. Player-stat `stats` can include numeric `rating` and/or `adr` (or `damage_per_round`), with a consistent `metric_version` (for example `rating-2.0`) and a date or month in `measurement_period`. Roster memberships accept `available_at` so historic cutoffs do not use transfers learned later.

`stage_entry` is `stage1`, `stage2`, or `stage3` and means a direct entrant to that phase. A complete field must describe 16 direct Stage 1 entrants and eight direct entrants at both Stages 2 and 3. Advancers are added by simulation. A complete real roster import can select **Replace synthetic demo teams** when the demo is active; it does not delete earlier user-supplied records. For a partial import, leave that option off. Imports are merged by stable IDs and stored locally. Tournament simulation remains disabled until 32 team records are present.

For imported completed Singapore Major matches, set `stage_slug`, `round_no`, `status: "completed"`, and a `winner_id` that is one of the two participants. The simulator preserves that result and any imported score. Confirmed but unresolved fixtures can use `status: "scheduled"`; their pairing is retained while the winner remains predicted. In the UI, **Set Result** records a user-entered outcome; `PUT /api/matches/{stable_match_id}/result` sets or edits it and `DELETE` clears it. A score is optional and must be in Team A:Team B map wins (for example `2:1`) and valid for the configured BO1/BO3/BO5 format. Every request includes the match result version, so an outdated editor receives a conflict instead of overwriting a newer change.

## Verification

```bash
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests -q
npm test
npm run build
```

The tests cover Swiss records and rematches, result set/edit/clear flows, upset progression and persistent records, duplicate-count prevention, downstream conflict confirmation, playoff progression, stale writes, scenarios, reproducible Monte Carlo probabilities, imported context features, roster dates, source labeling, and historical cutoff enforcement.

## Configuration

Edit `backend/data/demo_seed.json` to change stage dates, entrant counts, thresholds, match formats, or provisional seeds. The API validates entrant totals and returns an actionable configuration error when a stage cannot be simulated. The prediction cutoff, number of Monte Carlo runs (1–10,000), and random seed are available in the UI.

`backend/data/schema.sql` is the initial SQLite migration (version 1 is recorded in `schema_migrations`). To move to PostgreSQL, replace the small `sqlite3` persistence layer in `app/database.py` with a SQLAlchemy or equivalent repository while keeping the API and simulation contracts.

## Tournament workspace tools (version 1.2)

Open **Method & data → Tournament workspace**:

- **Manager:** edit teams, seeds, direct stage assignment, players, roster membership, and official scheduled fixtures using stable IDs. Preview runs the existing importer on a temporary in-memory database. A complete field is also checked for tournament consistency. Confirm to save; stale preview versions are rejected. The ordinary CSV/JSON file import also previews counts and warnings before saving. Editing a synthetic team retains its synthetic flag; import real teams with their real stable IDs. Existing completed fixtures must be edited through Set/Edit Result, not replaced by scheduled fixtures. Swiss stage schedules and opening/decider formats can be changed before that stage has results. These settings stay explicitly user configured; they do not certify unpublished organizer rules.
- **Changes & scenarios:** save the current forecast under a name; compare any two persisted runs. See championship odds changes, obsolete/new pairings, projected stage standings and champion. After Set Result, a compact “What changed?” panel appears automatically. Saved scenarios show when the official state has changed, and Recompute applies current confirmed results. Incompatible old overrides produce an error instead of silently changing their meaning. Recomputed scenarios point at the new run; their older immutable snapshots remain in forecast history.
- **Performance:** score the earliest saved, non-scenario forecast for each confirmed matchup, only when it was recorded strictly before the known match time. Unknown match times and synthetic teams are excluded by default; `/api/workspace/performance?include_demo=true` is an explicit diagnostic opt-in. Each match is counted once. Accuracy, Brier score, log loss, calibration counts and predicted/observed rates are calculated from saved probabilities. These operational scores are separate from chronological model-training holdout metrics. No eligible records means no accuracy estimate. Manual results with no known `played_at` are deliberately not scored.
- **Team profiles:** roster history, dated player measurements with metric definitions, recent results, head-to-head totals, map records, and the latest current-state projected tournament path. Empty sections explain missing coverage. H2H totals summarize up to 50 recent records; they are not the model's recency/roster-weighted H2H effect. Profiles show stored history; the prediction engine separately enforces the selected historical cutoff.
- **Map explorer:** enter the remaining map order after hypothetical bans for BO1/BO3/BO5. Per-map forecasts start with the Elo map probability; where both teams have history, a fixed 0.7 log-odds coefficient adjusts for Beta(4,4)-shrunk map rates. Missing maps use the baseline. Dynamic programming calculates first-to-N series probability, stopping when a winner is reached. This deliberately assumes independent maps with no side/veto-order effects and is **exploratory, not a validated prediction improvement**. It does not add map adjustments on top of the main model's existing map adjustment. It never writes official vetoes or results.
- **Backups & updates:** manual snapshots plus automatic snapshots before imports, result changes and stage-setting changes. Restore requires confirmation and the current state version, creates a recovery snapshot first, checks integrity, reapplies migrations and invalidates forecasts. Snapshots include results, imported records, prediction history and scenarios, and are excluded from Git. No retention limit is currently imposed; review disk usage as history grows. GitHub release checks need internet access; normal local work does not.

Schema migration **005_saved_scenarios.sql** runs automatically on startup. API requests are serialized through a shared file lock so local instances cannot restore a database while another API request updates or simulates it. This lock supports macOS/Linux; the desktop installer targets macOS. Keep the API on localhost for personal use; public deployment requires authentication and authorization around all write operations.

Additional tests cover read-only preview failures, synthetic provenance preservation, stale import/restore rejection, scenario freshness and recomputation, forecast timing and source separation, profile paths, map normalization, stage settings, and backup persistence. UI tests exercise preview-before-save, missing performance data, and restore cancellation.
