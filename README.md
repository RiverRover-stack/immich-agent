# ShutterStorm · Immich resilience lab

The [frontend dashboard](frontend/README.md) follows the ShutterStorm experiment lifecycle: discovery, baseline, planning, validation, controlled faults, observation, analysis, reporting and verified restoration.

```sh
cd frontend
npm ci
npm run dev
```

Open http://127.0.0.1:5173. Demo Mode uses explicitly simulated observations and never modifies Immich. The dashboard provides dependency maps, source-linked findings, prediction scorecards, queue/upload telemetry, rollback status and JSON/print reports.

The Python orchestration engine is not included in this checkout. Live integration requires the [proposed API bridge](frontend/docs/API.md), which maps the backend's flat-file artifacts to dashboard data. See [HANDOFF.md](HANDOFF.md) for current validation and remaining backend work.

Local credentials, Immich data, dependencies and build output are ignored by Git. Do not force-add them. Existing deployment files are separate from the dashboard.
