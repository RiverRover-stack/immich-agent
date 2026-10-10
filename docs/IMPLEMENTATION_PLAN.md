# ShutterStorm — Implementation Plan

*What we build, in what order, and how we know each piece is done.*
Written 10 Oct 2026, 14:50. **Day-1 clock is running.**

---

## Status (10 Oct 2026, evening): what is actually built

| # | Step | State | Evidence |
|---|---|---|---|
| 0 | Foundation: repo, Immich v3.3.1 pinned, bootstrap, seed | Done | 4/4 healthy, 100 photos, ML models cached |
| 1 | Probes (ping, auth, browse, search, upload + processing tracker) | Done | Verified against a real ML outage; search probe uses unique queries because Immich caches embeddings (O-1) |
| 2 | Discover / map + static audit | Done | `reports/topology.json`, `dependency-map.png`, 5 grouped findings |
| 3 | Fault library + executor + undo journal + kill switch | Done | 17 tests on a throwaway container; real crash recovery with `panic` |
| 4 | Guard + drill | Done | **Drill passed on live Immich**: aborted in 2.1 s, undo verified |
| 5 | Experiment E-01 end to end | Done | Reproduced the silent-processing-loss observation automatically |
| 6 | Baseline | Done | 5 min, 0% errors, p95 5-51 ms |
| 7 | LLM layer: client, planner, fallback | Done | Verified on `ministral-14b` / `ministral-8b`; falls back on any failure |
| 8 | Agent loop | Done | LLM-planned session on live Immich (see runs/) |
| 9 | Analyzer + report with number and claim checker | Done | 110 tests; evaluation report generated |
| 10 | Final evaluation flow (`shutterstorm evaluate`) | Done | 7/7 stages passed |
| - | Dashboard (frontend) | Deferred by decision D9 | CLI live view + HTML report instead |
| - | Network faults (`tc netem`), upload spike | Not built | Three failure types (stop, pause, cpu_throttle) meet the minimum |

**Still to do before submission:** fresh-clone test of the README; demo video; team form; architecture
page review against this status; one clean canonical run on the final code; commit and push.

---

## A note on time estimates

This plan deliberately carries **no per-step build-time estimates.** Code is written in minutes, so
coding is not the bottleneck. What actually costs wall-clock time is:

| Cost | Examples | Compressible? |
|---|---|---|
| **Measurement waits** | 5-minute baseline · cooldown until queues drain · a 30 s fault really lasts 30 s | **No** — settling time is the measurement |
| **Debug loops** | A probe reporting false failures; a fault that will not undo cleanly | Unpredictable — the real variable |
| **Verification** | Killing a container by hand to confirm the probes notice | Short but non-negotiable |

Plan around the **checkpoints** below, not around clock estimates. Each step is done when its
*Done when* check passes.

---

## Principle

Build **hands and eyes first, brain last.** The AI layer sits on top of the mechanical layer, so the
mechanical layer must work standalone. This also means the demo degrades gracefully instead of dying.

Every step below has a **Done when** — an observable check, not an opinion. Nothing is "done" because
it compiles.

---

## Step 0 — Foundation ✅ COMPLETE

| Item | Evidence |
|---|---|
| Repo skeleton, Makefile, licensing, `.gitignore` | `make help` lists all targets |
| Immich v3.3.1 pinned and reproducible | `scripts/fetch_immich.sh`, refuses non-pinned tags |
| Immich running | 4/4 containers healthy, `/api/server/ping` → `pong` |
| Localhost-only exposure | Port 2283 bound to `127.0.0.1` |
| Admin + API key automated | `make bootstrap` → key saved to `.env` (mode 600), verified |
| 100 photos seeded | `make seed` → 100 images, queues drained, 0 failed |
| ML verified | CLIP + face + OCR models cached (2.1 GB); smart search returns in ~35 ms |

---

## Day 1 — remaining work (priority order)

> **Day-1 minimum (organizers):** a running app ✅, a **dependency map**, and **one safe failure
> injection with a working kill switch**.

### Step 1 — Probes (the eyes)

Async loop, one coroutine per journey, appending to CSV.

| Journey | Request | Needs |
|---|---|---|
| J0 ping | `GET /api/server/ping` | server |
| J2 browse | timeline query + fetch a thumbnail | server, postgres |
| J3 search | `POST /api/search/smart` | server, **ML**, postgres |
| J5 auth read | `GET /api/users/me` | server, postgres |
| J1 upload | `POST /api/assets` every ~10 s, then poll until thumbnail exists | server, redis, postgres, ML |
| Queue watch | `GET /api/jobs` → waiting/active/failed | server, redis |

**Done when:** `shutterstorm probe --seconds 60` produces a CSV whose rows match reality — kill the ML
container by hand and the search rows flip to `ok=false` within a second.

**Known problem to solve:** Immich de-duplicates by file hash, so the upload probe cannot re-send the
same bytes. Fix by mutating one pixel / EXIF field per upload. **Verify the behaviour, don't assume it.**

### Step 2 — Discover / map (Day-1 deliverable)

Parse `immich/docker-compose.yml` → services, `depends_on`, volumes, healthchecks, restart policy,
resource limits. Merge with `journeys.yaml`. Emit `topology.json` + a rendered graph + a static audit.

**Static findings available without any chaos** (these are real, citable results):
- `immich-server` does not declare `immich-machine-learning` in `depends_on` → **hidden dependency**
- No resource limits on any service → one container can starve the others
- Single instance of everything → no redundancy
- `restart: always` only reacts to *exit*, not to a hung process

**Done when:** `shutterstorm discover` writes `topology.json` + graph image, and the audit lists the
four findings above.

### Step 3 — Fault library, tested on a dummy

`stop`, `pause`, `cpu_throttle` first (Docker SDK). Network faults after, if time allows.
Each fault implements `apply()` / `undo()` / `verify_undone()` / TTL.

**Rule from `CLAUDE.md`: every fault is exercised against a throwaway `nginx:alpine` container before it
is allowed anywhere near Immich.**

**Done when:** `pytest tests/test_faults.py` passes against the dummy container, and
`verify_undone()` genuinely detects a failed undo (test it by sabotaging the undo on purpose).

### Step 4 — Guard + kill switch + drill  ⛔ **HARD GATE**

Watchdog thread: reads live probe results, trips on threshold breach, rolls back from the journal.
Kill switches: STOP file, Ctrl-C, `panic`.

**Done when:** `shutterstorm drill` injects a mild fault with a deliberately low threshold, the guard
**auto-aborts within 10 s**, the fault is undone, `verify_undone()` confirms it, and the journal is empty.

**No fault runs against Immich until this passes.** This is also exactly what the slide means by
*"Automatic stop or rollback on threshold breach, tested at least once."*

### Step 5 — Experiment E-01 end to end

Stop `immich_machine_learning` for 30 s while uploads run.
**Expected:** search fails · uploads still return 201 · processing stalls · queues back up · recovery measured.

**Done when:** `runs/<id>/result.json` contains real numbers and a short clip is recorded for the
Day-1 checkpoint demo.

**Day-1 Definition of Done:** running app ✅ · `topology.json` + graph · drill proving the kill switch ·
E-01 executed with measurements · ~30 s clip.

---

## Day 2

### Step 6 — Baseline
5 min of steady load, no faults → `baseline.json` (per-journey error rate, p50/p95, queue idle).
Also the A/A control (E-00) to establish the noise floor.
**Done when:** two consecutive baselines agree within ~10 %.

### Step 7 — The LLM layer
Thin model-agnostic client (`llm/client.py`, one `complete_json()` entry point, provider adapters).
Planner prompt + pydantic schema + validator + **deterministic fallback**.
**Done when:** the planner produces a valid ranked experiment list **and** the whole run still works with
`LLM_API_KEY` deleted.

### Step 8 — Agent loop
Plan → validate → inject → observe → analyze → re-plan, unattended.
**Done when:** one command runs 3+ experiments back-to-back with no human input and no safety incident.

### Step 9 — Analyzer + report
Code computes every metric and the score; the LLM writes ranked findings; a checker verifies each cited
number against the data.
**Done when:** `reports/report.md` ranks findings with evidence IDs, severity and fixes — and the number
checker rejects a deliberately corrupted figure.

### Step 10 — Polish and submission
**Feature freeze ~4 h before the deadline.** Then: one clean canonical run on this machine · demo video ·
architecture page · team form · **fresh-clone test** · submit 30 min early.

### Stretch (only if everything above is done)
Dashboard · prediction scorecard · one fix + re-run showing score improvement · combined faults.

---

## Checkpoints — where we stop and rethink

| # | Checkpoint | Question | If it fails |
|---|---|---|---|
| 1 | After probes | Are the numbers believable? Does a hand-killed container show up? | Fix probes — every later number depends on them |
| 2 | After the map | Does the graph match reality? | Correct `journeys.yaml` by hand (allowed by the brief) |
| 3 | **After the drill** | Did the guard actually abort and roll back? | **Stop. Nothing proceeds until it does** |
| 4 | After E-01 | Did reality match the prediction? Did undo verify? | Revisit thresholds / fault implementation |
| 5 | After the first LLM plan | Is the output valid, safe and sensible? | Tighten the prompt and schema; fallback covers the demo |
| 6 | Day-2 feature freeze | Does everything work end to end from a clean start? | Cut scope — never add |
| 7 | Before submission | Fresh clone, follow the README | Fix setup docs |

---

## Who does what

| Person | Owns |
|---|---|
| **Lead (Parit)** | Chaos executor, guard, planner, analyzer — the core loop |
| **T1 Setup** | Immich on every machine at the same version; share the 2.1 GB model cache |
| **T2 Probes** | Journey probes from the template; CSV correctness |
| **T3 Observer** | Manual fault exploration notes early; report template + README later |
| **T4 Product** | Repo hygiene, architecture slide, team form, demo video, dashboard |

Teammates on <8 GB RAM machines develop against the dummy container, not real Immich.

---

## Risks

| Risk | Mitigation |
|---|---|
| LLM API unavailable or unconfirmed by organizers | Deterministic fallback planner + recorded-run replay |
| Live demo flakiness | Pre-record a backup clip of a successful run |
| Guard false-trips constantly | Tune thresholds against the A/A control first |
| ML model reload makes recovery look slow | Model cache is a named volume — never delete it; measure and report it honestly |
| Upload probe blocked by de-duplication | Mutate each file before upload; verify early |
| Time overrun | Feature freeze is a hard stop, not a suggestion |
