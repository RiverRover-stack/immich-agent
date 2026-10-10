# Frontend validation — 10 October 2026

Validated on Windows with Node.js 22.13.0 and headless Google Chrome.

- `npm test`: **20 tests passed**. Covers missing drill/baseline gates, predictions before injection, acknowledgement versus impact, honest fallback provenance, score calculation, protected-journey abort, stop idempotence, journal retention, cleanup retry, TTL and CPU bounds, allowlist, active-run exclusion, API rejection/disconnection, demo-field exclusion, incompatible bootstrap, malformed history and premature completion.
- `npm run build`: **passed** TypeScript and Vite production compilation (2,289 modules). Graph and chart bundles remain split; no oversized-chunk warning.
- `npm run test:browser`: **passed all eight scenarios**. Verified prediction scorecard, queue chart, upload overlay including ML, failure snapshot, report dialog and JSON download, CPU input limits, Escape dismissal, desktop rendering and 390px mobile rendering. Six graph nodes were asserted to be inside the resized canvas. No browser runtime errors or horizontal mobile overflow.
- Desktop and mobile screenshots were visually inspected. The mobile graph nodes are visible after resize. Screenshots remain ignored in `frontend/test-results/desktop.png` and `mobile.png`.
- `npm ci` completed and its audit reported **zero vulnerabilities** across 163 audited packages.
- Dependency installation and Node/Vite tests required execution outside the Windows sandbox because sandbox file resolution returned EPERM. They completed with the required permissions.

Live engine operations were not end-to-end tested: this checkout contains no ShutterStorm Python engine or HTTP bridge. Unit tests exercise live-adapter contract rejection and disconnect behavior. Real Docker faults, safety drill, guard thresholds, server authorization, planner APIs, raw artifact mapping and undo correctness must be verified in the backend sandbox.

The demo is an accelerated fixture, not a benchmark or evidence of Immich behavior. No container configuration or data was modified during frontend validation.
