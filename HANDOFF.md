# ShutterStorm frontend handoff

Last updated: 2026-10-10

## Current state

The frontend dashboard is implemented in `frontend/` inside the `immich-agent` Git repository. It defaults to a safe, deterministic Demo Mode and does not execute shell commands, modify Docker, or change the Immich deployment.

Implemented views and features:

- Run Console with environment/connection status, Start Run, Stop & Restore, overview cards, workflow state, telemetry, journey checks, and detail tabs.
- Interactive React Flow dependency map with pan/zoom/fit, service inspection, snapshots, health, fault highlighting, and configured demo journey overlays.
- History & Reports with search and filters, full report dialogs, JSON download, print/PDF support, and patch download.
- Setup drawer with approved catalogue faults, backend-supplied-style parameters, checkpoint, bounded repair paths, protected paths/tests, confirmation window, attempt limit, hypothesis, readiness checks, and validation.
- Nine-step workflow state machine with explicit pending/running/passed/failed/skipped states.
- Separate outcome and cleanup state. A run is complete only after cleanup verification passes.
- Deterministic scenarios for first-attempt recovery, second-attempt recovery, exhausted attempts, baseline failure, Stop & Restore, cleanup retry, scope violation, inconclusive confirmation, and readiness failure.
- Typed models for runs, phases, services, dependencies, faults, repair attempts, tests, evidence, patches, metrics, and cleanup.
- Demo and Live adapters. Live Mode reports failures and never silently falls back to simulated success.
- Same-origin, HTTP(S)-only evidence attachment handling with safe image preview types.
- Responsive desktop and mobile layouts, visible focus styles, dialog focus trapping, keyboard Escape handling, and text/icon status indicators.
- Local demo history persistence. Live data is not stored in localStorage.

The simulated repair is intentionally plausible and bounded: it corrects an approved machine-learning endpoint in `deploy/immich-config.json` from port `3999` to `3003`. This is simulated evidence only; the file is not changed on disk.

## Important files

- `frontend/src/App.tsx` — main views, forms, reports, tabs, polling, reconnect behavior, and exports.
- `frontend/src/Graph.tsx` — dependency graph, snapshots, overlays, node inspection, and responsive refit behavior.
- `frontend/src/demo.ts` — deterministic workflow engine and demo scenarios.
- `frontend/src/adapters.ts` — Demo and Live data adapters.
- `frontend/src/types.ts` — typed API/domain models.
- `frontend/src/workflow.test.ts` — lifecycle and safety regression tests.
- `frontend/browser-check.mjs` — full UI regression script for all demo scenarios.
- `frontend/docs/API.md` — proposed backend contract and backend obligations.
- `frontend/docs/VALIDATION.md` — validation record.
- `frontend/docs/THIRD_PARTY.md` — frontend dependency/license list.
- `frontend/README.md` — setup, demo walkthrough, Live Mode, and integration notes.
- `README.md` — repository entry point.
- `.gitignore` — prevents local secrets, Immich data, dependencies, and build output from being committed.

## Validation completed

The latest production build passed after the final responsive graph change:

```sh
cd frontend
npm run build
```

Result: TypeScript passed, Vite transformed 2,289 modules, and emitted split application, graph, and chart bundles with no oversized-chunk warning.

Earlier in the same session:

- `npm test` passed all 14 tests.
- The browser regression passed all nine demo scenarios.
- The browser regression checked the changed-file panel, failure topology snapshot, history/report dialog, JSON report download, desktop view, and 390px mobile layout.
- No browser runtime errors or horizontal mobile overflow were found.
- `npm audit --omit=dev` reported zero vulnerabilities after updating Vitest and adding Recharts' explicit `react-is` dependency.

The final responsive graph change added a `ResizeObserver` so React Flow refits after the viewport width changes. The production build passed after this change. The full browser regression was not rerun after this very last small adjustment; rerunning it is the first recommended task below.

## Remaining frontend work

These are the only checks or refinements left on the frontend side:

1. Start the development server and rerun the browser regression after the final graph resize adjustment:

   ```sh
   cd frontend
   npm run dev
   # In another terminal:
   npm run test:browser
   ```

   The script expects Google Chrome and `http://127.0.0.1:5173`.

2. Visually inspect `frontend/test-results/mobile.png` and confirm that the graph nodes are visible after the `ResizeObserver` update. The previous mobile screenshot showed the graph canvas but its nodes were not visible after changing viewport size; this is why the resize/refit fix was added.

3. Optionally rerun the final complete validation sequence:

   ```sh
   cd frontend
   npm test
   npm run build
   npm audit --omit=dev
   ```

4. Review the untracked files before committing. The repository currently shows the dashboard files, `.gitignore`, root `README.md`, and the pre-existing `docker-compose.yml` as untracked. Confirm whether `docker-compose.yml` should be committed; it existed before this frontend work and was not authored or modified as part of the dashboard task.

5. Commit and push when satisfied. Do not force-add `.env`, `library/`, `postgres/`, `frontend/node_modules/`, `frontend/dist/`, `frontend/test-results/`, or TypeScript build metadata. They are intentionally ignored.

## Remaining live integration work

The repository does not contain a ShutterStorm orchestration backend. Demo Mode is complete, but Live Mode requires implementing the API described in `frontend/docs/API.md`:

- Environment and readiness checks.
- Clean checkpoint creation/validation.
- Backend-owned approved fault catalogue and bounded parameter schemas.
- Dependency discovery with authoritative `source: "discovery"` topology.
- Create/start run, current run state, run history, and complete reports.
- Backend guards, TTL, cancellation, rollback journal, and restoration verification.
- Baseline and protected-test execution.
- Fault acknowledgement separate from failure confirmation.
- Evidence, metric, attachment, test, and dependency snapshot collection.
- Repair orchestration constrained to allowed paths.
- Server-side scope validation and all required acceptance checks.
- Stop & Restore and cleanup retry endpoints with idempotent server behavior.
- Authentication, authorization, CSRF protection, HTTPS, safe CORS, and sensitive-log controls.
- Optional SSE/WebSocket event streaming. The current frontend uses polling as the documented fallback.

The backend must enforce every safety constraint independently of the browser. The existing Immich API is not the ShutterStorm orchestration API.

## Running the dashboard

```sh
cd /Users/asechanchib/Documents/immichimage/immich-agent/frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`.

For Live Mode, copy `.env.example` to `.env`, set `VITE_MODE=live` and `VITE_API_URL=/api/v1`, and restart Vite. Never put secrets in `VITE_*` values because they are public browser configuration.

## Git safety note

At handoff time, the repository has no committed frontend changes yet; the new frontend and documentation are untracked. Local credentials and Immich data are ignored. Review with:

```sh
git status --short --untracked-files=all
git check-ignore .env library postgres frontend/node_modules frontend/dist frontend/test-results
```

No Git commit or push was performed.
