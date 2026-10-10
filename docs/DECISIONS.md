# ShutterStorm — Design Decisions

*Why we chose what we chose. Each entry: the question, the decision, the reasoning, and what would
change our mind.*

---

## D1 — Where do the "config facts" come from?

**Decision:** three sources, all read-only, never guessed.

| Source | How | Example fact |
|---|---|---|
| `immich/docker-compose.yml` | Parsed as YAML | `immich-server.depends_on = [redis, database]` — ML absent |
| Docker runtime | `docker inspect` via the SDK | actual restart count, health status, memory limit = 0 (unlimited) |
| Immich's OpenAPI spec (v3.3.1) | Downloaded at the pinned tag | which endpoints exist, their exact request bodies |

Every fact is traceable to a file we can show a judge. A hypothesis ("so uploads will stall") is
*derived* from facts but is **not** a fact until an experiment proves it. The report keeps the two
visually separate.

---

## D2 — Do we need a database?

**Decision: no.** Flat files — CSV for time series, JSON for structured state.

**Why:**
- Single machine, single run at a time, append-only data. A DB solves none of our problems.
- CSV and JSON are **inspectable** (open in any editor) and **diff-able** — judges can see the raw evidence.
- Zero operational burden. A hackathon has no time for schema migrations or another service to babysit.
- Artifacts can be committed as the canonical run for deliverable #4.

**What would change our mind:** needing cross-run queries for a dashboard. Then SQLite — one file, no
server, still committable. Not Postgres.

**Important distinction:** Immich's own `immich_postgres` and `immich_redis` are **chaos targets**, not
our storage. We never write to them. Measuring Immich with Immich's own database would mean our
measurement tooling dies exactly when the thing we are measuring dies.

---

## D3 — LangChain, or the provider SDK?

**Decision: neither as a framework — a ~100-line client of our own,** with provider adapters.

**Why not LangChain:**
- It abstracts away the two things we care most about: the exact prompt sent, and the exact JSON returned.
- When output fails validation we need to see precisely what came back. Framework layers obscure that.
- It is a large dependency with fast-moving APIs for what is, in our case, two prompt types.
- `CLAUDE.md` requires a **model-agnostic** client driven by env vars; a thin adapter achieves this directly.

**What we build instead:**

```
shutterstorm/llm/client.py
    complete_json(prompt, schema) -> validated object
    providers: mistral | anthropic | openai-compatible | paste | fallback
```

Provider and model come from `LLM_PROVIDER` / `LLM_MODEL`. Swapping providers is an env change.
Since Mistral is the likely provider and exposes an OpenAI-compatible endpoint, one adapter may cover
several providers.

---

## D4 — What happens when the LLM API is not available?

**Decision:** three layers, chosen automatically in order.

| Layer | When it is used | What it does |
|---|---|---|
| **1. API** | Key present and responding | Normal agentic operation |
| **2. Paste mode** | Key unusable, human present | Tool prints the prompt, a human pastes a model's reply back; schema validation is identical |
| **3. Formula fallback** | No key, no human | Deterministic scoring ranks experiments; templated report |

**The formula:**

```
priority = 0.40·(journeys depending on it) + 0.25·(stateful) + 0.20·(single instance) + 0.15·(untested)
```

Selection is automatic — not a mode anyone has to choose — and the run never stops. Every experiment
records **which planner produced it**, and the report states this plainly.

### Honesty requirement (non-negotiable)

Paste mode is legitimate — a real language model still makes the decision, and the hackathon brief does
not require a particular API vendor. **But the submission must describe what actually happened.** If the
canonical run used paste mode or the fallback, the report and video say so. We never claim autonomous
API operation we did not perform. Deliverable #5 (team form) lists the real models used.

---

## D5 — How much should the LLM drive?

**Decision: the LLM drives all *decisions*; code keeps all *hands and eyes*.**

The model is given a set of safe operations ("buttons") rather than a shell:

| Allowed | Forbidden |
|---|---|
| Read the map, metrics, results | Executing arbitrary commands |
| Choose a target and fault from the catalog | Choosing a target outside the allowlist |
| Request an abort | Disabling the guard |
| Write findings | Inventing numbers |

Every button validates its own arguments before acting. **The guard is never AI-driven** — it is fixed
threshold logic, because it is the last line of defence and must behave identically every time.

---

## D6 — Can this tool work on any application?

**Short answer: yes for the mechanics, no for the meaning — and that split is the design.**

**Already app-agnostic:** the fault library (any Docker container), the guard, the executor, the
analyzer, the scoring, the report, and compose parsing.

**App-specific:** what a *user journey* is, and how to call the app's API. That is roughly one adapter
file plus `journeys.yaml` per target app.

So pointing ShutterStorm at a different compose-based app means writing a journeys file and a small
probe adapter — not rewriting the engine. Immich is the proving ground, not a hardcoded assumption.
We will not build extra target adapters during the hackathon, but we keep the boundary clean so the
claim is honest and demonstrable.

---

## D7 — Repo license

**Decision: Apache-2.0** (the team's existing `LICENSE`, from the initial commit).
`CLAUDE.md` originally said MIT; it has been updated to match reality.
Immich is AGPL-3.0 — downloaded at setup time, run unmodified, never vendored into this repo, and
attacked only from the outside.

---

## D8 — Pinned Immich version

**Decision: v3.3.1, pinned everywhere.** `fetch_immich.sh` refuses anything that is not an exact
`vX.Y.Z` tag.

**Why:** Immich's own docs use `releases/latest/...`. With `latest`, two teammates can silently run
different code, and a release mid-hackathon would invalidate our measurements. Compose file, images and
OpenAPI spec all come from the same tag.

---

## D9 — CLI-first, no frontend

**Decision:** everything is a terminal UI plus generated files. No web dashboard during the hackathon.

**Why:**
- Fewer moving parts on stage — a dashboard adds a server, a port and a browser that can all fail live.
- A terminal showing real commands and live numbers reads as more credible than a UI that could be faked.
- Terminal output records cleanly for the 3-minute video and stays legible after compression.
- Build time goes into the engine instead of presentation.

**What this looks like:**
- **Live:** a `rich` auto-updating table per experiment — per-journey error rate, p95 vs baseline,
  accepted-vs-processed counter, queue depths, guard state, and the STOP instruction on screen.
- **After:** `probes.csv`, `result.json`, `topology.json` + graph image, and `report.md` / `report.html`.

The generated HTML report is a static document, not a frontend — no server, no build step. It exists
because deliverable #4 reads better that way.

**Not thrown away later:** a dashboard would read the same CSV files, so adding one afterwards costs
nothing we have already built. It remains a stretch goal only.

---

## D10 — Manual, assisted and automatic modes

**Decision:** one engine, three ways to drive it.

| Mode | Command | Who picks the fault | Who approves |
|---|---|---|---|
| Manual | `shutterstorm inject <target> <fault> --ttl N` | Human | Human |
| Assisted | `shutterstorm run --approve` | Agent proposes | Human, per experiment |
| Auto | `shutterstorm run` | Agent | Nobody (guard still active) |

**Why:** a demo needs a presenter in control of timing; manual mode is a fallback if the LLM API is
unavailable; and showing one fault by hand before handing over to the agent makes the autonomy visible.

**Safety invariant:** manual injections use the *same* path as agent ones — validator, undo journal,
guard, TTL. There is no side door. A human typing `inject` cannot exceed the allowlist or the time
limit, which raw `docker stop` can.

**Consequence for build order:** the fault library and the `inject` command come before the agent, and
are what we use to verify that probes detect real failure.

---

## D11 — Which models, and why not the ones we first chose

**Decision:** planner = `ministral-14b-latest` (role `large`), report writer = `ministral-8b-latest`
(role `small`), both via Mistral's API. Chosen by measurement on 10 Oct 2026, not by name.

**What we found** (`shutterstorm llm-check` reproduces it):

| Model | Result on our key | Requests/min |
|---|---|---|
| `mistral-large-latest` | HTTP 403, not in the subscription tier | n/a |
| `mistral-small-latest`, `mistral-medium-latest`, `magistral-*` | HTTP 429 with `x-ratelimit-limit-req-minute: 0` | 0 |
| `ministral-14b-latest` | works | 30 |
| `ministral-8b-latest` | works | 188 |
| `ministral-3b-latest` | works | 750 |

Quotas are **per model**; a model being listed by `/models` does not mean the key can use it.

**Measured on the models we use:** valid JSON against our `Proposal` schema 3/3 for both; correct
native tool call; did not obey an instruction injected through log text; ~2.5 s trivial call, ~6 s for
a full plan. 30 req/min is ample (one planner call per experiment).

**Why this is safe to change later:** roles, not model names, are what the code depends on. Moving to
small/large is two lines in `.env` plus `shutterstorm llm-check`.

---

## D12 — The LLM never writes a number

**Decision:** report figures come from code; the small model only writes prose, and a checker
verifies every figure it cites.

- The writer receives a structured evidence bundle and deterministic draft findings.
- Each returned finding is checked: cited evidence ids exist; every number appears in the evidence
  (percent and rounded forms allowed; small counts 0-3 and 100 always allowed; ids/versions ignored);
  severity is not raised above the most severe draft for the same evidence; no draft is dropped.
- One retry with the rejection reasons; then the deterministic template findings are used.
- The report records which writer produced the text, so it can never overstate the AI's role.

**Why:** a plausible-sounding invented figure is the failure that would sink the report's credibility.
The checker was itself verified: it caught a bug in its own severity logic during testing.

---

## D13 — "Upload" is two journeys: accepted, and processed

**Decision:** `upload` (HTTP 201) and `upload_processing` (thumbnail **and** search index exist) are
separate journeys in the dependency map.

**Why:** they fail independently. With ML stopped, uploads are still accepted (201) and thumbnailed,
but are never search-indexed, while queue counters show no failures (observations O-2). A single
"upload" journey either hides that (if it only checks acceptance) or mispredicts (if it claims to need
ML). Splitting them makes the map predict the real behaviour, so the prediction scorecard is fair.

---

## D14 — Experiments come from a catalog, not from code

**Decision:** `experiments/catalog.yaml` lists, per target, which faults to run and in which order,
plus default parameters and TTLs. The fallback planner ranks *targets* by the specified formula and
takes the first not-yet-run, allowed fault from the catalog.

**Known limitation (documented, not hidden):** the specified formula counts dependents, statefulness,
single-instance and untestedness. It does not reward a hidden (undeclared) dependency, so ML ranks last
on formula alone. This is exactly the gap the LLM planner is expected to close, and the report shows
which planner chose each experiment.

---

## Open questions

| Question | Status | Blocks |
|---|---|---|
| Which LLM providers do the organizers allow? | **Unanswered — ask early** | Final provider choice (Mistral assumed) |
| Exact Day-1 checkpoint time and Day-2 deadline | Unknown | Scheduling the feature freeze |
| Judging rubric | Unknown | Where to invest polish |
| Submission portal details | Unknown | Deliverable packaging |
