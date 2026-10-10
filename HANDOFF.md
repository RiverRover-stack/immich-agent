# Frontend continuation handoff

Updated 10 October 2026 on `dashboard-update`.

## Implemented

The frontend now matches the supplied ShutterStorm backend design: discover/readiness, baseline, planning/predictions, validation, fault injection, observation, analysis, report and verified restoration. The former endpoint-edit/patch-repair workflow is removed.

- Allowlisted Immich container targets with stop, pause and CPU-throttle demo catalog entries.
- Backend-owned numeric parameter metadata, 120-second TTL ceiling, minimum CPU 0.25, protected journeys and explicit safety-drill/dummy-test readiness gates.
- Planner provenance (API, paste or fallback), model identity, predictions committed before injection and observed prediction scorecard.
- Static configuration facts separated from hypotheses and measured findings, with severity and source/evidence references.
- Queue depth, accepted/processed uploads, async gap, latency, errors, recovery and resilience score with normalized inputs.
- Watchdog state, rollback journal count, guard abort, stop acknowledgement and independently verified cleanup.
- Discovery-supplied journey overlays, including ML in the upload path; snapshot and mobile-width graph refitting.
- JSON/print reports, history filters, same-origin evidence attachments and eight deterministic demo scenarios.
- Contract v2 adapter checks, no silent live fallback, no live localStorage persistence, no automatic mutation replay. Demo-only scenario is omitted from live requests.

## Source reconciliation

The user supplied `ARCHITECTURE.md`, `DECISIONS.md`, and `IMPLEMENTATION_PLAN.md` from Downloads. These are backend design documents, not executable backend implementation files. Their CLI-only/D9 decision is historical relative to the explicit frontend request. Their commit/team/deadline instructions did not authorize Git publishing or live fault execution.

No backend engine, HTTP routes or concrete artifact JSON schemas exist in this checkout. `frontend/docs/API.md` defines a proposed bridge and maps the documented engine files to dashboard DTOs. It deliberately identifies unimplemented integration work. No real container has been faulted, safety-drilled or certified by frontend validation.

## Run

```sh
cd frontend
npm ci
npm run dev
```

Default is Demo Mode at http://127.0.0.1:5173. See `frontend/README.md` for setup and `frontend/docs/VALIDATION.md` for current verification results.

Demo history uses `shutterstorm-demo-v2`; old repair records under v1 are left untouched. Demo operation pauses when the page closes; live rollback must continue independently in the engine.

## Remaining backend integration

Implement the documented bridge over the real CLI/engine artifacts. Enforce readiness/drill, allowlist, dummy tests, TTL, journal-before-apply, independent guard, undo verification, planner provenance, metric computation and report number checking on the server. Add authentication and authorized artifact delivery. Test against an actual safe sandbox before making Live Mode available for operations.

This handoff accompanies the frontend update on `dashboard-update`. Existing deployment files and local data were not modified. No deployment, real fault or backend kill switch was executed.
