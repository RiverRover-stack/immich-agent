# ShutterStorm

**AI-driven chaos engineering for cloud-native resilience, proven on [Immich](https://github.com/immich-app/immich) v3.3.1.**

ShutterStorm plans controlled failures, injects them with safety limits and a kill switch, observes the
impact, and reports ranked weaknesses with fixes. It only ever targets a local sandbox.

> The AI proposes; code disposes. An LLM chooses what to break and explains what it found, but every
> action passes an allowlist, a validator and a guard, and every number in the report is computed by code.

## What it does

```
map the app ─▶ baseline ─▶ plan (LLM or formula) ─▶ validate ─▶ inject ─▶ observe ─▶ undo + verify
        ▲                                                │           (guard watches live)
        └────────────── re-plan from the result ─────────┘            ─▶ analyze ─▶ report
```

| Piece | What it is |
|---|---|
| **Probes** | Fake users: ping, auth, browse, search, upload (and "was the upload actually processed?") |
| **Faults** | `stop`, `pause`, `cpu_throttle`: each reversible, with a TTL and a write-ahead undo journal |
| **Guard** | Fixed-rule watchdog: aborts and rolls back on error-rate / latency breach or unexpected container death |
| **Planner** | LLM proposes ranked experiments with predictions; deterministic formula planner is the fallback |
| **Analyzer** | Error rate, p95, recovery time, accepted-vs-processed gap, prediction accuracy, resilience score |
| **Report** | Ranked findings (Markdown + HTML). The LLM writes prose; a checker verifies every figure it cites |

## Safety

Nothing runs against anything but the four Immich containers on `127.0.0.1`.

- **Allowlist** of 4 containers, enforced by the planner's validator *and* again by the executor
- **TTL** on every fault (max 120 s); faults undo themselves
- **Write-ahead undo journal**: `shutterstorm panic` restores everything after a crash
- **Kill switches**: `shutterstorm stop`, `touch .shutterstorm/STOP`, Ctrl-C, SIGTERM
- **Drill**: `make drill` trips the guard on purpose and proves it rolls back. Run it before real experiments
- The target address is refused unless it is `127.0.0.1` / `localhost`

## Requirements

- Linux, macOS, or Windows via WSL2
- Docker Engine with Compose v2 (>= 2.24) and your user allowed to run `docker`
- Python 3.11+
- ~8 GB free RAM and ~10 GB disk for Immich (first start downloads about 3 GB of images/models)

## Setup

```bash
git clone <repo-url> shutterstorm && cd shutterstorm

python3 -m venv .venv && source .venv/bin/activate    # fish: source .venv/bin/activate.fish
make install

cp .env.example .env                  # then see "LLM (optional)" below

make immich-fetch                     # downloads the pinned Immich v3.3.1 compose + env into immich/
make pull                             # large download; do this on good Wi-Fi
make up
make health                           # waits until all 4 containers are healthy and the API pings
```

Open <http://127.0.0.1:2283>, create the admin account, then:

```bash
make bootstrap                        # creates an API key and saves it to .env (asks for your login)
mkdir -p data/sample_images           # put some .jpg files here (they are git-ignored)
make seed                             # uploads them
make discover                         # writes reports/topology.json + reports/dependency-map.png
```

Wait until Administration → Jobs shows idle queues (the first run loads ML models).

## Run

```bash
make baseline        # 5 min of steady-state probes -> runs/baseline.json
make drill           # PROVE the guard works (must print DRILL PASSED)
make evaluate        # baseline -> ML fault -> restore -> verify -> compare -> report
make run             # the agent loop: plan -> inject -> observe -> analyze -> report
make cleanup         # delete the photos the probes uploaded
```

Manual control, for demos and exploration (same safety gate as the agent):

```bash
shutterstorm faults                                  # what can be injected, and where
shutterstorm inject ml stop --ttl 30                 # crash the ML service for up to 30 s
shutterstorm inject db pause --ttl 20
shutterstorm experiment ml stop --ttl 30             # measured: probes + guard + recovery
shutterstorm stop                                    # KILL SWITCH (latched until `resume`)
shutterstorm resume
shutterstorm panic                                   # undo anything left in the journal
shutterstorm run --approve                           # agent proposes, you confirm each one
```

Everything a run produces is a plain file under `runs/<timestamp>-…/`: `probes.csv`, `queues.csv`,
`uploads.csv`, `events.json`, `results.json`, `report.md`, `report.html`.

## LLM (optional)

Without a key the tool still runs end to end on deterministic fallbacks. To use a model, set in `.env`:

```
LLM_PROVIDER=mistral
LLM_BASE_URL=https://api.mistral.ai/v1      # the base URL only, not .../chat/completions
LLM_API_KEY=...
LLM_MODEL_LARGE=ministral-14b-latest        # planner
LLM_MODEL_SMALL=ministral-8b-latest         # report writer
```

Then `make llm-check`. Quotas are **per model**: a model that is listed may still be unusable on your
plan, so check before relying on it. The report states which planner and writer produced each part.

## Layout

```
shutterstorm/   discover/ workload/ chaos/ guard/ planner/ analyzer/ report/ llm/  + cli.py
scripts/        fetch_immich.sh  bootstrap.py  seed.py  wait_healthy.sh
experiments/    catalog.yaml          which faults to run per target, in what order
journeys.yaml   what each user journey needs (hand-maintained, checked against the probes by tests)
deploy/         compose override: localhost-only port + the allowlist labels
docs/           TEAM_BRIEF  ARCHITECTURE  IMPLEMENTATION_PLAN  DECISIONS  OBSERVATIONS  THIRD_PARTY
tests/          pytest: faults run against a throwaway container, never Immich
```

Run the tests with `make test` (the fault tests need Docker and pull `nginx:alpine`).

## Documentation

| Doc | What it covers |
|---|---|
| [`docs/TEAM_BRIEF.md`](docs/TEAM_BRIEF.md) | **Start here**: the whole project in one page |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Components, how the AI decides, safety controls |
| [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) | Build order, done-when checks, status |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Why we chose what we chose |
| [`docs/OBSERVATIONS.md`](docs/OBSERVATIONS.md) | What we actually saw, with evidence and caveats |
| [`docs/THIRD_PARTY.md`](docs/THIRD_PARTY.md) | Every library, image, model and data source |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `make up` says run `make immich-fetch` | `immich/` is missing: run `make immich-fetch` |
| `refusing non-local target` | `IMMICH_URL` must be `http://127.0.0.1:2283` |
| `kill switch is latched` | Intentional after `shutterstorm stop`: run `shutterstorm resume` |
| `N fault(s) from a previous run are not undone` | Run `shutterstorm panic` |
| Search probes always fail right after `make up` | ML is still loading models; wait for `make health` |
| LLM 429 with `limit-req-minute: 0` | That model has no quota on your key: pick another in `.env`, `make llm-check` |
| `.env` changes ignored by Immich | Immich reads `immich/.env`; recreate with `docker compose up -d --force-recreate` |

## License

ShutterStorm is licensed under Apache-2.0. Immich is AGPL-3.0: it is downloaded at setup time, run
unmodified, and never vendored into this repository.
