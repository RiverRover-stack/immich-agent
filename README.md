# ShutterStorm · Immich resilience lab

The dashboard lives in [`frontend/`](frontend/README.md). Existing Immich deployment files remain separate.

```sh
cd frontend
npm ci
npm run dev
```

Open http://127.0.0.1:5173. The default **Demo Mode** is a complete local simulation; it does not modify the Immich deployment. Use Start Run to choose a scenario, inspect evidence and repairs, and verify cleanup. History provides downloadable JSON, readable print/PDF reports and patch exports.

See [frontend setup and walkthrough](frontend/README.md), [proposed backend API](frontend/docs/API.md), and [third-party software](frontend/docs/THIRD_PARTY.md).

Local `.env` credentials, `library/`, `postgres/`, dependency folders and build outputs are ignored by Git. Commit the frontend source, lockfile and documentation along with `.gitignore`; do not add local data or credentials with `git add -f`.
