# ShutterStorm dashboard

A React + TypeScript dashboard for the approved fault → evidence → bounded repair → verification → cleanup workflow. Demo Mode is the default. The frontend never executes shell commands, Docker operations or code changes.

## Run

Requires Node.js 22.13+ and npm.

```sh
cd immich-agent/frontend # from the parent workspace; use cd frontend inside the repo
npm ci
npm run dev
```

Open the local URL printed by Vite (normally http://127.0.0.1:5173).

```sh
npm test
npm run build
npm run preview
```

## Demo walkthrough

1. Select **Start Run**. Inspect scope, checkpoint, approved fault and readiness.
2. Choose a scenario and start. Each simulated backend phase advances approximately every 2.4 seconds while the dashboard is open. The simulated confirmation window and metric clock are accelerated; elapsed wall time is shown separately.
3. Inspect topology snapshots, configured journey overlays, telemetry, evidence, attempts, changed files and protected tests.
4. For the stop drill, click **Stop & Restore** while the run is active. Cancellation acknowledgement is followed by restoration and verification; it is not instant success.
5. For cleanup failure, click **Retry cleanup verification** after the failure. A verified patch does not make the run complete until cleanup passes.
6. Open **History & Reports**, filter runs, and open the report for JSON, print/PDF, and patch downloads. Unverified proposals are labelled. Demo reset clears local demo history.

Nine scenarios cover first-attempt recovery, second-attempt recovery, exhaustion, blocked baseline, manual cancellation, cleanup retry, invalid scope, inconclusive confirmation, and blocked readiness. Select a two-attempt budget for the successful-retry scenario. The repair changes a simulated `deploy/immich-config.json` inference endpoint from port 3999 back to 3003. No such file is changed on disk, and this is not a claim about the running deployment's configuration.

Demo history (up to 40 runs) is stored under `shutterstorm-demo-v1` in localStorage. Storage failure leaves the session functional. Live results are never persisted in browser storage. Demo simulation pauses while the page is closed and resumes when reopened.

## Live integration

Copy `.env.example` to `.env` and set:

```
VITE_MODE=live
VITE_API_URL=/api/v1
```

Restart Vite after changing configuration. Serve the API on the same origin via your reverse proxy, or explicitly configure backend CORS for the dashboard origin. Do not place credentials in `VITE_*` variables: these values are public browser configuration. Authentication belongs in the backend/session layer.

There is no implemented orchestration API in this repository. Live Mode requires the [proposed API contract](docs/API.md); it fails visibly without that service and never substitutes demo data. The existing Immich API is not the ShutterStorm orchestration API. Polling is used because no SSE/WebSocket contract is implemented. After a disconnect, **Reconnect & fetch current state** reloads bootstrap, run history and authoritative active-run state before resuming polling. Mutations are never automatically replayed.

## Structure

- `src/types.ts`: API entities and workflow statuses.
- `src/demo.ts`: deterministic state machine, approved demo catalogue and deployment-shaped topology.
- `src/adapters.ts`: simulated backend and HTTP backend adapter.
- `src/App.tsx`: run setup, console, history, reports and accessible dialogs.
- `src/Graph.tsx`: React Flow graph, snapshots and inspection.
- `src/workflow.test.ts`: safety and lifecycle regression checks.

Remaining integration work: implement server-side readiness, checkpoint handling, catalogue validation, fault guards, bounded repair orchestration, protected-test execution, scope/required checks, artifact collection, discovery, and restoration verification. The backend must enforce these constraints even when requests bypass this UI. Add your authentication and authorization; none are simulated as real credentials here.

Browser regression: with `npm run dev` running and Google Chrome installed, run `npm run test:browser`. It uses headless Chrome and a virtual clock, exercises all nine scenarios and report downloads, and writes ignored desktop/mobile screenshots to `test-results/`.
