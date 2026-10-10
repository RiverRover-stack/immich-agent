# ShutterStorm — Team Brief

**One page to understand the whole project.** Read this first. Written 10 Oct 2026.

---

## 1. What we are building, in one sentence

An **AI agent that breaks our own photo app on purpose, safely**, watches what fails, and writes a
ranked report of the weaknesses it found with recommended fixes.

## 2. Why this is not just "randomly killing containers"

Anyone can run `docker stop`. The hackathon asks for an **AI that decides**:

| Question | Who answers it |
|---|---|
| **What** should we break? | The AI, after mapping the app and ranking what is most fragile |
| **When** and for how long? | The AI, within hard limits code enforces |
| **How safely**? | Code — a guard that auto-aborts, a timer, an undo journal |
| **What did it reveal**? | The AI, using only numbers that code measured |

Our one-line rule: **AI proposes, code disposes.**

## 3. The target: Immich

[Immich](https://github.com/immich-app/immich) v3.3.1 — a self-hosted Google-Photos clone. Four containers:

| Container | What it does |
|---|---|
| `immich_server` | The API (port 2283) + background job workers |
| `immich_machine_learning` | AI features: smart search, face detection, OCR |
| `immich_postgres` | Database — users, photos metadata, search vectors |
| `immich_redis` | Job queue (upload → process chain) |

**Why Immich is a good victim:** it has a hidden weak spot. `immich-server` does *not* declare
`immich-machine-learning` as a dependency in its compose file — but search needs it. So the system
*thinks* it is healthy while a real user feature is broken.

**Our headline story:** an upload can return **HTTP 201 Created** while processing **silently stalls**.
The user thinks the photo is saved. The system says everything is fine. Neither is true.

## 4. The six roles of the system

| Role | Plain-English job | Code or AI? |
|---|---|---|
| **Mapper** | Works out what the app is made of and what depends on what | AI reads, code verifies |
| **Planner** | Decides which failure to try next, and predicts what will break | **AI** |
| **Executor** | Actually breaks it — and can undo it | Code |
| **Guard** | Lifeguard. Stops everything if it gets dangerous | Code (never AI) |
| **Observer** | Fake users + stopwatch. Measures errors, latency, recovery | Code |
| **Analyst** | Turns measurements into ranked findings and fixes | Code computes, **AI** writes |

## 5. Two terms you need

**Probe** — a tiny fake user. A loop that does one action over and over and records the result:

```
every 1s:  search "a dog"  →  record: timestamp, ok/fail, latency_ms
```

We run one per user journey, so at any moment we know *"can users actually use the app right now?"*

**Journey** — one thing a real user does, plus what it needs underneath:

```
browse  → server → postgres
search  → server → ML → postgres
upload  → server → postgres + redis   (then ML later, for processing)
```

Containers being "up" is not the same as users being happy. Journeys measure the second one.

## 6. The safety design (this is what the judges look for)

Nothing touches Immich until all of this exists and is tested:

1. **Allowlist** — only the 4 Immich containers can ever be targeted. Anything else is rejected.
2. **TTL** — every fault has a timer (max 60 s default). It ends even if the tool crashes.
3. **Undo journal** — the undo recipe is written to disk *before* the fault is applied.
4. **Auto-abort** — the guard rolls everything back if a protected journey's error rate > 10 % for
   10 s, or latency p95 > 3× baseline for 10 s, or a container dies unexpectedly.
5. **Kill switches** — `shutterstorm stop`, `touch .shutterstorm/STOP`, Ctrl-C, and
   `shutterstorm panic` (undoes everything in the journal).
6. **The drill** — we deliberately trip the guard to prove the rollback works. **Required before any
   real experiment.**

**Hard boundaries, never crossed:** our own machines only · never a real or production system ·
never anything shared with other teams · we never modify Immich's code · we never fill the host disk.

## 6b. Manual, assisted and automatic

The same engine can be driven three ways: **manual** (`inject`, a human picks), **assisted**
(`run --approve`, the agent proposes and a human confirms) and **auto** (`run`). Manual injections go
through the same safety gate as agent ones. See D10 in `docs/DECISIONS.md`.

## 7. What is already working (verified 10 Oct 2026)

- Immich v3.3.1 running, 4 containers healthy, bound to `127.0.0.1` only, 100 photos, ML models cached
- **Probes** for five journeys, including "was the upload actually processed?"
- **Faults** (stop, pause, cpu_throttle): reversible, timed, journaled; **kill switch**, `panic`, `drill`
- **Guard drill passed on the live system**: the guard aborted a fault in 2 s and the undo was verified
- **Dependency map** with the hidden ML dependency highlighted, plus a static configuration audit
- **AI planner** (Mistral `ministral-14b`) chose the right first experiment unprompted; the formula
  planner takes over automatically if the API fails
- **Report writer** (`ministral-8b`) whose every figure and claim is machine-checked
- **`shutterstorm evaluate`**: the full baseline -> fault -> restore -> verify -> compare -> report flow
- 110 automated tests

### What we have found so far (single runs; see `docs/OBSERVATIONS.md` for caveats)

1. **Accepted uploads that are never processed.** With ML down, uploads return success and get
   thumbnails but are never search-indexed, even after ML returns, and the queue counters show nothing wrong.
2. **A repeated search hides an ML outage**, because Immich caches query embeddings.
3. **Stopping Redis stops background processing** with only a warning (accidental observation).

## 8. The five deliverables

| # | Deliverable | Owner | Status |
|---|---|---|---|
| 1 | Public repo + README (setup, how to run) | T4 Product | README stub exists |
| 2 | Demo video ≤ 3 min | T4 Product | Not started |
| 3 | Architecture overview (1 page) | Lead | `docs/ARCHITECTURE.md` |
| 4 | Findings report from a real run | Lead | Needs experiments first |
| 5 | Team form (members, tools, AI models, libraries) | T4 Product | `docs/THIRD_PARTY.md` feeds this |

## 9. Where to look next

| I want to know... | Read |
|---|---|
| What we build and in what order | `docs/IMPLEMENTATION_PLAN.md` |
| How the system is designed | `docs/ARCHITECTURE.md` |
| Why we chose X over Y | `docs/DECISIONS.md` |
| Every library/image/model we use | `docs/THIRD_PARTY.md` |
| The rules Claude Code must follow | `CLAUDE.md` (repo root) |
| How to set up on my machine | `README.md` |
