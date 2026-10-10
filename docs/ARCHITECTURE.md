# ShutterStorm — Architecture Overview

*Deliverable #3. Components · how the AI decides · safety controls.*

---

## The closed loop

```
                    ┌─────────────────────────────────────────────┐
                    │                                             │
                    ▼                                             │
   ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌──────────┐  ┌────────────┐
   │ DISCOVER │─▶│ PLANNER  │─▶│ VALIDATOR │─▶│  CHAOS   │─▶│  OBSERVER  │
   │  (map)   │  │  🤖 AI   │  │  (gate)   │  │(execute) │  │  (probes)  │
   └──────────┘  └──────────┘  └───────────┘  └────┬─────┘  └─────┬──────┘
        ▲                                          │              │
        │                                     ┌────▼──────────────▼────┐
        │                                     │   IMMICH SANDBOX       │
        │                                     │  server · ML · pg · redis│
        │                                     └────────────────────────┘
        │                                          ▲              │
        │                                   abort &│rollback      │
        │                                     ┌────┴─────┐        │
        │                                     │  GUARD   │◀───────┘
        │                                     │(watchdog)│
        │                                     └──────────┘
        │              ┌──────────┐  ┌──────────────┐
        └──────────────│ ANALYZER │─▶│   REPORT     │
            re-plan    │ (numbers)│  │  🤖 AI words │
                       └──────────┘  └──────────────┘
```

🤖 = the only two places an LLM is involved.

## Components

| Component | Responsibility | Output |
|---|---|---|
| `discover` | Parse compose + inspect runtime + load journeys → dependency graph, static config audit | `topology.json`, graph PNG/HTML |
| `workload` | Probes for each user journey; upload tracker; job-queue watcher | `probes.csv` (ts, journey, ok, status, latency_ms) |
| `planner` | Rank hypotheses; choose the next experiment with predictions | `Experiment` JSON (pydantic-validated) |
| `validator` | Allowlist + parameter clamping between planner and executor | accept / reject / clamped |
| `chaos` | Fault library: `apply()`, `undo()`, `verify_undone()`, TTL | fault applied + journal entry |
| `guard` | Independent watchdog: thresholds, container events, kill switches | abort signal + rollback |
| `analyzer` | Deltas vs baseline, recovery time, async-completion gap, score | `Result` JSON |
| `report` | Ranked findings with evidence, severity, fixes; verifies the writer's figures and claims | `report.md` + `report.html` |
| `llm` | Model-agnostic client: roles `large` (planner) and `small` (writer), schema-validated JSON, retries, key never logged | validated objects |
| `evaluate` | One command that runs the whole flow and checks each stage | `stages.json`, report, evidence |

## How the AI decides

**Input given to the model** (facts only, as structured JSON):
topology + journeys · baseline metrics · fault catalog with hard bounds · results of experiments so far.

**Output required from the model** (schema-validated):

```json
{ "target": "immich_machine_learning", "fault": "stop", "params": {"duration_s": 30},
  "hypothesis": "uploads are accepted but never processed while ML is down",
  "predictions": [ {"journey": "search", "expect": "fail",    "confidence": 0.9},
                   {"journey": "upload", "expect": "healthy", "confidence": 0.7} ],
  "protected_journeys": ["browse"],
  "reason": "ML is absent from immich-server's depends_on, so nothing signals the dependency" }
```

**Ranking logic (criticality):** how many user journeys depend on the component × how unprotected it
is (single instance, stateful, no resource limits, restart-only-on-exit) × whether we have tested it yet.
After **every** experiment the model re-plans with the new evidence, so the order adapts to what it learns.

**Predict-then-test:** the model commits to a prediction *before* the fault runs. The report scores its
accuracy — this is how we show the AI reasons rather than guesses.

### Guardrails on the AI

| Guardrail | Effect |
|---|---|
| Allowlist | Only the 4 Immich containers are valid targets; anything else is rejected outright |
| Parameter clamps | `duration ≤ 120 s`, `delay ≤ 2 s`, `loss ≤ 30 %`, `cpus ≥ 0.25` |
| Schema validation | Malformed output is rejected and retried, never executed |
| No shell access | The model selects from a fixed catalog; it never emits commands |
| Number checking | Every figure the model cites in the report is verified against measured data (percent and rounded forms allowed) |
| Claim checking | Words the evidence cannot support (`data loss`, `corrupt`, `cascade` without measured collateral damage) are rejected |
| Severity ceiling | The writer cannot rate a finding more severe than its deterministic draft |
| Provenance | Every experiment and the report record which planner / writer produced them |
| Deterministic fallback | If the API is unavailable, a scoring formula plans instead and the run continues |

## Safety controls

| Control | Mechanism |
|---|---|
| **Blast radius** | Allowlist of 4 containers; localhost-only binding; never touches the host disk |
| **Time limit** | Every fault carries a TTL (default 60 s, hard max 120 s) and self-expires |
| **Write-ahead undo** | The undo recipe is persisted to `.shutterstorm/faults.json` *before* apply; removed only after `verify_undone()` |
| **Auto-abort** | Protected-journey error rate > 10 % for 10 s · p95 > 3× baseline for 10 s · unexpected container exit · max duration |
| **Kill switches** | `shutterstorm stop` · `touch .shutterstorm/STOP` · Ctrl-C handler · `shutterstorm panic` (undo everything) |
| **Proof it works** | `shutterstorm drill` trips the guard deliberately and demonstrates rollback — required before any real experiment |
| **Tested in isolation** | Every fault is exercised against a throwaway `nginx:alpine` container before it is allowed near Immich |

## Where decisions live

- **Which faults, in what order, per target:** `experiments/catalog.yaml` (data, not code).
- **What each user journey needs:** `journeys.yaml`, hand-maintained, checked against the probes by a test.
- **Thresholds the guard uses:** `GuardConfig` (error rate 10 % over 10 s; p95 > 3x baseline with a 150 ms
  noise floor; unexpected container exit). The guard is a thread inside the experiment process.
- **Hard bounds on anything an LLM proposes:** `planner/validator.py` and `chaos/faults.py`.

## Measurements

| Metric | Definition |
|---|---|
| Error rate | Failed probe requests ÷ total, per journey, per 1 s window |
| Latency p50/p95 | From probe timings, compared against baseline |
| Recovery time | Seconds from undo until all journeys are within tolerance for 10 consecutive 1 s windows |
| Async completion gap | Uploads accepted (HTTP 201) − uploads actually processed (thumbnail exists) |
| Blast radius | Which journeys degraded vs. only the targeted one |
| Resilience score | `100 × (0.35·availability + 0.20·recovery + 0.20·async_completion + 0.15·integrity + 0.10·blast_radius)` |

## Data flow and storage

No database. Flat files, human-inspectable and diff-able:

```
topology.json     the dependency map
baseline.json     steady-state reference
runs/<id>/probes.csv      one row per probe request
runs/<id>/result.json     computed metrics per experiment
.shutterstorm/faults.json undo journal (write-ahead)
reports/report.md|html    deliverable #4
```

## Stack

Python 3.11+ · `httpx` (async probes + LLM calls) · `docker` SDK (faults) · `pydantic` v2 (schema gate) ·
`networkx` (graph) · `typer` + `rich` (CLI) · `jinja2` (report) · `pytest` (tests) ·
Mistral `ministral-14b` (planner) and `ministral-8b` (writer) behind our own ~200-line client (no LangChain) · `matplotlib` (map image). Network faults via a `tc netem` sidecar are designed but not built.
