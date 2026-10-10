# ShutterStorm dashboard API bridge — contract v2

This checkout contains the frontend, not the Python orchestration engine. The supplied ARCHITECTURE.md, DECISIONS.md and IMPLEMENTATION_PLAN.md specify a CLI and flat-file artifacts, not HTTP routes. The endpoints below are a **proposed bridge contract**, not claims about an existing backend API or exact artifact schemas. `src/types.ts` defines the dashboard DTOs. Do not point the dashboard at Immich's API.

The former repair/patch contract has been replaced by the discover → baseline → plan → validate → inject → observe → analyze → report → restore workflow. Automated code repair is outside the supplied backend design. D9's historical CLI-only decision is superseded for this frontend task; the engine stays CLI-first and file-backed.

## Routes

Paths are relative to `VITE_API_URL` (default `/api/v1`; the envelope uses `contractVersion: 2`).

| Method | Path | Body / response |
| --- | --- | --- |
| GET | `/bootstrap` | `Bootstrap`, contractVersion 2, discovered topology, source-linked audit findings, journey names, environments with drill/readiness state, tested fault catalogue |
| GET | `/runs` | `Run[]`, newest first, including active and cleanup-failed experiments |
| POST | `/runs` | `Setup` without `scenario`; returns accepted `Run` |
| GET | `/runs/:id` | Full authoritative `Run` snapshot |
| POST | `/runs/:id/stop` | `{}`; acknowledges cancellation, returns `Run` with restoration pending |
| POST | `/runs/:id/cleanup/retry` | `{}`; retries restoration and verification, returns `Run` |

The browser polls every 800ms, with one outstanding poll and a 10s timeout. Mutations are never automatically replayed. After errors, reconnect reloads bootstrap, history and the current experiment. POST acknowledgement is not evidence of failure, cancellation completion or successful undo. Stop/retry must be idempotent server-side. Return 409 for conflicting operations and 422 for invalid input. Responses always contain JSON, including mutation responses.

## Engine-to-dashboard mapping

| Engine data | Dashboard DTO | Integration responsibility |
| --- | --- | --- |
| `topology.json` + compose/runtime audit | `Bootstrap.topology`, `findings` | Map service IDs/positions and source references; use `source: discovery`. Journey overlays must come from journeys.yaml, never a browser guess. |
| `baseline.json` | `Run.tests` baseline period, `analysis.baselineP95Ms` | Preserve probe units, baseline duration and evidence. |
| Planner Experiment | `setup`, `plan` | Map `target`, `fault` → faultId, `params.duration_s` → duration; remaining numeric params → parameters; `protected_journeys` → protectedJourneys. |
| Planner metadata | `plan.producer`, `model`, `reason`, `predictions` | API, paste or fallback explicitly recorded. Freeze predictions before injection; fill observed/matched only after analysis. |
| `runs/<id>/probes.csv` | tests, journeys, metrics | Aggregate real 1-second windows. `latency` is per-window p95 in milliseconds; errors use percent 0–100. Never average percentiles to create a whole-run percentile. |
| Upload tracker / queue watcher | accepted, processed, gap, queue | Gap = accepted uploads minus completed thumbnail processing; queue backlog is separately collected. Missing values are null, never zero. |
| `runs/<id>/result.json` | analysis, findings, outcome | Backend computes whole-window error rate/p50/p95, recovery and normalized score inputs; browser displays supplied results. |
| `.shutterstorm/faults.json` + watchdog | guard, cleanup | Journal entry count and verified undo supplied by engine. Preserve entries until verify_undone succeeds. |
| report.md / report.html / raw artifacts | evidence attachments | Proxy authorized artifacts through same origin. Generated HTML is linked, never executed in the dashboard. |

The listed engine filenames are specified by the supplied architecture; exact on-disk JSON schemas have not been supplied. Implement mappings against actual engine output. No database is needed. One Run DTO represents one experiment; a backend closed-loop campaign can publish each experiment separately into history, with new plans reflecting prior evidence.

## Safety and results

- Only `immich_server`, `immich_machine_learning`, `immich_postgres`, `immich_redis` are allowed targets. Resolve actual runtime names server-side. The browser never runs shell or Docker commands.
- Fault catalog entries are keyed by `(target, id)`. Numeric parameters include value, min, max and step. Start is disabled without readiness, a passed drill, tested fault and protected journeys. The server must enforce all prerequisites again immediately before apply.
- Default TTL is 60s; the E-01-style demo uses 30s. Hard max 120s. CPU >=0.25, delay <=2000ms, loss <=30%. Only advertise capabilities that have passed dummy-container testing; network faults are optional.
- Persist the undo recipe before applying. Independently enforce TTL, STOP file, CLI stop/panic, Ctrl-C, and guard thresholds (protected errors >10% for 10s, p95 >3x baseline for 10s, unexpected exits, max duration). A disconnected browser cannot disable the guard.
- Phase indexes follow `phases` in types.ts. Readiness/baseline failures skip injection. Failed observation confirmation produces `inconclusive`; guard trips produce `aborted`. Measurements are not equivalent to a successful repair.
- `status: complete` requires `cleanup: passed`, `guard.undoVerified: true` and `guard.journalEntries: 0`. Cleanup failure leaves `status: cleanup-failed`, preserving outcome and journal. Recovery/score stay null until sufficient verified observations exist.
- Recovery is measured from undo until ten consecutive healthy 1s windows. `analysis.asyncGap` is peak fault-window gap; metric gap is instantaneous. Score = 100 × (0.35 availability + 0.20 recovery + 0.20 asyncCompletion + 0.15 integrity + 0.10 blastRadius). Supply the normalized inputs used; normalization remains engine-owned.
- Configuration facts must cite compose/runtime/spec sources. Hypotheses remain unproven; measured findings cite evidence IDs. Rank findings by severity. Model prose and number checking belong to the backend; the browser does not fabricate missing metrics or run an LLM.

## Transport and deployment

Use a same-origin reverse proxy to the bridge, HTTPS and HttpOnly session authentication as appropriate. Enforce authorization and CSRF checks on mutations. `VITE_*` values are public; never place API keys there. Live history is not stored in localStorage. The API rejects incompatible contract versions and the frontend rejects demo topology and premature completion.

Attachment URLs must be same-origin HTTP(S); only PNG/JPEG/WebP are previewed. Serve other artifacts with Content-Disposition and nosniff. Never include credentials or private model reasoning in responses. Logs are rendered as text.
