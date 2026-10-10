# Proposed ShutterStorm API v1

This is a proposed frontend integration contract, not an existing Immich endpoint. TypeScript wire types are defined in `src/types.ts`. All timestamps are ISO 8601. All run reads return the complete `Run` aggregate, including events, evidence, metrics, tests, attempts, patches and snapshots. No response may include credentials or private model reasoning.

| Method | Path (relative to VITE_API_URL) | Response |
| --- | --- | --- |
| GET | `/bootstrap` | `Bootstrap`: environments/readiness, approved faults and discovered topology |
| GET | `/runs` | `Run[]`, newest first, including any active run |
| POST | `/runs` | `Run`, accepted create/start result; body `Setup` |
| GET | `/runs/:id` | Authoritative `Run`; polled while active |
| POST | `/runs/:id/stop` | `Run` with cancellation requested; empty JSON body |
| POST | `/runs/:id/cleanup/retry` | `Run` with cleanup running; empty JSON body |

The aggregate is also the JSON report. Patch retrieval is through `Run.patches[].diff`; tests, evidence and metrics are part of the same snapshot. Evidence may include `attachments: [{name, url, mimeType}]`. The UI permits only HTTP(S) attachment URLs on the dashboard origin, previews PNG/JPEG/WebP, and displays other supplied attachments as links. Proxy authorized artifacts through the same origin; reject unsafe content types and serve downloads with Content-Disposition and nosniff headers. Arbitrary log text is never interpreted as an attachment URL.

## Backend obligations

- Validate environment, checkpoint, approved catalogue fault/parameters, duration (10–300s), attempts (1–5), allowed paths and protected tests. The scenario field is demo-only and must be ignored/rejected in live requests.
- Current parameter schema supports approved fixed-value strings. Do not interpret any field as a command. Extend catalogue metadata and UI validation together if bounded editable parameter types are needed.
- Check authorization and scope server-side. Return 409 for conflicting active runs and 422 for invalid configuration, with a safe rejection reason in the response body.
- Preserve exact nine-step order in `steps`, matching `phases` in types.ts. Readiness/baseline failure skips injection and leads to restoration verification. `injected` means injector acknowledgement, not proof of impact.
- Confirmation timeout yields `inconclusive` and cleanup. Mark observed evidence separately from predictions and configured journey mappings.
- Verification must include scope, protected tests and all required backend checks. A passing test suite alone does not permit `verified=true` on a patch.
- Failed verification may retry only below maxAttempts. Exhaustion saves unresolved results. Verified and unresolved paths both restore the checkpoint.
- `status=complete` is valid only with `cleanup=passed`. Cleanup failure leaves `status=cleanup-failed`, retaining the independently determined outcome. Stop acknowledgement must not claim restoration; subsequent reads track real cleanup progress.
- Enforce cancellation, TTL/guard rollback and recovery on the backend independently of browser presence or connectivity. POST /stop and cleanup retry should be idempotent server-side.
- Use `source=discovery` for Live topology. Health includes update timestamps; missing measurements use null or unavailable. Never infer causal attribution from graph connectivity.
- Tests identify period (`baseline` or `attempt N`), timestamp, duration, failure message and optional evidence ID. Evidence is escaped as text by React; HTML execution is never allowed.

## Transport / errors

HTTP polling runs approximately every 800ms with one outstanding poll. Requests time out at 10s. No automatic mutation retry is performed after a timeout or rejection: acceptance may be ambiguous. Reconnect reloads server state before polling resumes. Return an accurate list of runs including active operations to allow recovery after ambiguous create responses. All non-2xx responses are displayed as failures. Live bootstrap rejects demo topology. There is no silent demo fallback.

SSE/WebSocket is not currently advertised by the backend, so polling is the supported fallback. To add streaming, keep the adapter boundary, use monotonic event sequence IDs, and refetch the run snapshot on reconnect before applying subsequent events.

## Deployment

Use HTTPS and same-origin HttpOnly session cookies in production, enforce CSRF protection for mutations and appropriate CORS. `VITE_API_URL` is public. Store no secrets or sensitive logs in browser localStorage. Run history is server-owned in Live Mode. JSON export and print are explicit user actions.
