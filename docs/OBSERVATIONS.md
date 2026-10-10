# Observations log

*Things we actually saw, with evidence. Newest last. This is raw material for the findings report
(deliverable #4) — nothing here is a conclusion until it is reproduced by an automated experiment and
its numbers come from `result.json`.*

Rules: observed ≠ hypothesised. Every entry says how it was produced, what exactly was seen, and what
was NOT verified.

---

## O-1 — Immich caches search embeddings, which masks an ML outage for repeated queries

**Date:** 10 Oct 2026 · **Produced by:** manual `shutterstorm inject ml stop --ttl 12` + direct API calls
**Immich:** v3.3.1, all four containers healthy before the fault.

| Query while ML was stopped | Result |
|---|---|
| One seen before (`a dog`, `sunset`) | HTTP 200 in ~10 ms |
| A new query (`purple elephant on a bike`, a random uuid) | HTTP 500 `Failed to search smart` in 8–26 ms |

**Consequence for our tooling:** a probe that repeats a fixed query list reports search as *healthy*
during an ML outage. The probe now appends a random suffix so every request needs ML.

**Not verified:** where the cache lives or how long entries survive; whether the cache is keyed on the
exact string only. (The behaviour was confirmed; the mechanism was inferred from latency and results.)

**Why it matters beyond testing:** a real user re-running a recent search sees nothing wrong during an
ML outage, so the outage can go unnoticed by anyone using spot checks.

---

## O-2 — Uploads during an ML outage are accepted, then silently never indexed

**Date:** 10 Oct 2026 · **Produced by:** `shutterstorm probe` (75 s, upload every 10 s) with a manual
`shutterstorm inject ml stop --ttl 25` starting ~15 s in. Run data: probe output of that session.

| What | Observed |
|---|---|
| Uploads made while ML was down | 3 of 8; **all returned HTTP 201** |
| Thumbnails | Generated normally (0.1–1.0 s) |
| Smart-search index | **Never created** — `POST /search/smart {queryAssetId}` stayed HTTP 400 |
| After ML recovered | The 3 stayed un-indexed; uploads made *after* recovery indexed in < 1 s |
| Queue counters (`/api/jobs`) | **No waiting, no active, no failed** for smartSearch, faceDetection, ocr — before, during and after |
| Server log | `ERROR … Unable to run job handler (SmartSearch): Machine learning request … failed for all URLs` (same for AssetDetectFaces, OCR) |

So the failure is visible **only in the server log**. The user-facing upload succeeded, the queue
dashboard is clean, and the photos are permanently missing from content search until someone acts.

**Remediation tried:** `PUT /api/jobs/smartSearch {"command":"start","force":false}` ("run missing
jobs"). All 3 stuck assets were indexed within 2 s.

**Not verified:**
- Whether Immich has a scheduled job that would eventually catch these up (we waited a few minutes only).
- Whether faceDetection and OCR assets are affected the same way (the log shows their handlers
  erroring; we only checked the smart-search index directly).
- Reproducibility. This is n=1 manual run; the automated E-01 must reproduce it before it is a finding.

**Draft ranking input for the report:** severity high (silent data-path loss, no operator-visible
signal); fix = alert on handler errors / run the "missing" job after an ML recovery event.

---

## O-3 — Stopping Redis stops background processing with only a warning

**Date:** 10 Oct 2026 · **Produced by:** *accidental* — a smoke test of the `inject` command used a
real alias and was interrupted mid-fault. Recovered cleanly with `shutterstorm panic`.

| What | Observed |
|---|---|
| Server log while Redis was down | A flood of `missing 'error' handler on this Redis client` |
| Job processing | `WARN … No microservices worker is connected. Background jobs will not be processed until one is running.` |
| After Redis returned | `LOG … Microservices worker connected.` — reconnected without a manual restart |
| Duration | ~20 s outage |

**Not verified:** anything about uploads during a Redis outage (E-04 will test that), how long the
reconnect takes for longer outages, and whether queued jobs are lost.

**Process note:** this was an unplanned fault run. It is useful as a data point, but it is **not** an
experiment — no probes were running and no baseline existed. It is recorded for transparency and to
motivate E-04, not as evidence.


---

## O-4 — Automated session, LLM-planned: four crash experiments (10 Oct 2026, ~15:55)

**Produced by:** `shutterstorm run --planner llm --max 4` (planner `ministral-14b-latest`, TTL 60 s each,
guard armed, all undo verified). Raw evidence: `runs/20261010-155424-session/E-0*/`. Scores below
are from re-analysis of that raw evidence (`shutterstorm report`), after fixing a scoring flaw.

| Exp | Fault on | Journeys that failed | Recovery | Score | Prediction accuracy |
|---|---|---|---|---|---|
| E-01 | machine-learning | search, upload_processing (6 uploads accepted, 0 processed) | 1.9 s | 73 | 0.83 |
| E-02 | postgres | auth_read, browse, search, upload | 5.6 s | 52 | 0.83 |
| E-03 | redis | none of the reads; the 1 upload attempted in the window failed | 0.0 s | 71 | 0.17 |
| E-04 | server | everything | 2.6 s | 45 | 0.83 |

**What this adds beyond O-1..O-3**

- E-01 reproduces O-2 automatically: uploads accepted during an ML outage are never indexed.
- E-03 contradicts the O-3 hypothesis for *reads*: ping, auth, browse and search were unaffected by a
  60 s Redis outage (30-60 requests each, 0 errors). The LLM had predicted they would fail.
- E-03 upload evidence is **n = 1** (one attempt, which failed; the next, after Redis returned, took
  8.4 s). Treat "uploads need Redis" as a strong hint, not a result, until re-run.
- Every system recovered by itself within 6 s of the fault being undone (Docker restarts the
  container; all four are `restart: always`).

**Caveats (do not skip)**

- One run per experiment (n = 1). All four faults were the same type (`stop`).
- Predictions were produced by the same LLM that chose the experiments, before each run; accuracy is
  per-experiment and the sample is tiny.
- The first version of the scorer gave E-03 a score of 91 because an upload that was *refused* was
  not counted as a failed attempt. That is fixed (regression test added), and the 91 should never be
  quoted.
