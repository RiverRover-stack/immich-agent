# ShutterStorm dashboard

React + TypeScript frontend for the ShutterStorm discover, predict, validate, inject, observe, analyze and restore workflow. Aligned with the supplied backend architecture and implementation plan. Demo Mode is the default; all demo findings, safety results and numbers are explicit fixtures. No shell, Docker, real fault injection or automated code editing occurs in the browser.

## Run and validate

Requires Node.js 22.13+ and npm. From the repository root:

```sh
cd frontend
npm ci
npm run dev
```

Open http://127.0.0.1:5173. Validation:

```sh
npm test
npm run build
# With the development server running and Google Chrome installed:
npm run test:browser
```

## Walkthrough

1. Start Run, choose an allowlisted container, tested fault, bounded parameters, TTL and protected journeys. Readiness and safety drill are shown explicitly.
2. Watch the nine phases. Predictions are recorded before injection; injection acknowledgement precedes observed impact.
3. Explore dependency snapshots and journey overlays, queue depth, upload acceptance versus completion, prediction accuracy and source-linked findings.
4. Stop & Restore acknowledges the request, then verifies undo. Cleanup failure keeps the journal and prevents a new run until Retry cleanup verification succeeds.
5. Open History & Reports to filter by text, outcome, target or UTC start date, inspect the report, download JSON or print/PDF. Backend-produced raw artifacts can appear as evidence attachments.

Eight scenarios cover ML recovery, formula fallback, baseline failure, manual stop, cleanup retry, watchdog abort, inconclusive impact and a missing drill. All use the deterministic planner; no model API call is implied. Demo wall time is accelerated independently of the one-second fixture windows. Score normalization is illustrative; live normalization is engine-owned.

Demo state pauses while the page is closed and resumes on reopening. Up to 40 records are stored under `shutterstorm-demo-v2`; the old repair demo's v1 records are left untouched and not interpreted as experiment data. Reset demo is blocked during active or unverified cleanup.

## Live integration

There is **no Python orchestration backend or HTTP bridge in this checkout**. The supplied backend documents describe a CLI and flat files, not HTTP endpoints. [API.md](docs/API.md) defines the proposed v2 dashboard DTO contract and mapping from engine artifacts. Live Mode does not simulate success when the bridge is absent.

Copy `.env.example` to `.env`, set `VITE_MODE=live` and `VITE_API_URL=/api/v1`, then restart Vite. Serve the bridge through a same-origin proxy. Keep credentials out of VITE variables. Reconnect reloads server state before resuming; mutations are never replayed automatically. Backend kill switches remain available independently of this dashboard.

## Main files

- `src/types.ts`: experiment DTOs and phase order.
- `src/demo.ts`: simulation, fixtures, bounded catalog and validation.
- `src/adapters.ts`: local demo and live HTTP adapters.
- `src/App.tsx`: setup, console, evidence, findings, reports and history.
- `src/Graph.tsx`: responsive graph, discovered journey mappings and inspection.
- `src/workflow.test.ts`, `browser-check.mjs`: safety, contract and browser regression checks.

The old patch-repair UI has been removed because the supplied backend chooses bounded faults and measures resilience, rather than editing configuration files. No engine deployment or real safety drill has been performed by these frontend tests.
