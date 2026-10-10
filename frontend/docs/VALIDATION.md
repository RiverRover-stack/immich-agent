# Validation

Validated on 2026-10-10 with Node 22.13.1, npm 10.9.2, and local headless Google Chrome.

- `npm run build`: passed TypeScript and Vite production build. Graph and chart bundles are split; no oversized-chunk warning.
- `npm test`: 14 tests passed, covering baseline/readiness blocking, retry limits, scope rejection despite passing tests, verified/unresolved cleanup, cancellation acknowledgement, cleanup failure/retry, injection versus confirmation, inconclusive outcomes, approved parameters, live network failure, backend rejection and discovery isolation.
- `npm run test:browser`: all nine demo scenarios passed through the real UI. Verified changed-file display, failure topology snapshot, history/report opening, JSON download, and desktop/mobile screenshots. No browser runtime errors or horizontal mobile overflow at 390px.
- npm installation audit after patched Vitest and explicit react-is dependency: zero vulnerabilities. Final `npm audit --omit=dev`: zero vulnerabilities.
- Git ignore verification: `.env`, `library/`, `postgres/`, `frontend/node_modules/`, and `frontend/dist/` are ignored. Screenshots and TypeScript build metadata are also ignored.

Browser screenshots are generated at `test-results/desktop.png` and `test-results/mobile.png` and are intentionally not committed. The browser script expects the development server on 127.0.0.1:5173 and an installed Google Chrome.

Live operations were not end-to-end tested because this repository has no orchestration backend. Adapter rejection and disconnect behavior is covered by tests. Backend readiness, authorization, guards, checkpoints, fault execution, repair execution and restoration must be implemented and verified before enabling real experiments.
