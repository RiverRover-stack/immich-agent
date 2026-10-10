# Third-Party Components

*Every library, container image, model and data source we use. Feeds deliverable #5 (team form).*
**Rule: add an entry here the moment you add a dependency.**

---

## Target application (not ours, not modified)

| Component | Version | License | Use |
|---|---|---|---|
| [Immich](https://github.com/immich-app/immich) | v3.3.1 (pinned) | AGPL-3.0 | The sample app we test. Downloaded at setup, run unmodified, never vendored |

## Container images

| Image | Tag | License | Use |
|---|---|---|---|
| `ghcr.io/immich-app/immich-server` | v3.3.1 | AGPL-3.0 | Immich API + job workers |
| `ghcr.io/immich-app/immich-machine-learning` | v3.3.1 | AGPL-3.0 | Smart search, faces, OCR |
| `ghcr.io/immich-app/postgres` | 14-vectorchord0.4.3-pgvectors0.2.0 | PostgreSQL License | Immich's database |
| `docker.io/valkey/valkey` | 9 | BSD-3-Clause | Immich's job queue (Redis fork) |
| `nicolaka/netshoot` | latest | Apache-2.0 | Network-fault sidecar (`tc netem`); shares the target's network namespace |
| `nginx:alpine` | latest | BSD-2-Clause | Throwaway dummy container for testing faults safely |

## ML models (downloaded by Immich, cached in the `model-cache` volume, ~2.1 GB)

| Model | Purpose | Source |
|---|---|---|
| CLIP ViT-B-32 (openai) | Smart search (text → image) | `immich-app/ViT-B-32__openai` |
| buffalo_l | Face detection + recognition | `immich-app/buffalo_l` |
| PP-OCRv5 mobile | Text recognition in photos | PaddleOCR |

## Python libraries (ours)

| Library | License | Use |
|---|---|---|
| `httpx` | BSD-3-Clause | Async HTTP for probes and the Immich API |
| `docker` | Apache-2.0 | Container control for fault injection |
| `pydantic` v2 | MIT | Schema validation, especially of LLM output |
| `networkx` | BSD-3-Clause | Dependency graph |
| `typer` | MIT | CLI |
| `rich` | MIT | Terminal output |
| `pyyaml` | MIT | Compose + journeys parsing |
| `jinja2` | BSD-3-Clause | Report rendering |
| `Pillow` | MIT-CMU | Image handling for the upload probe |
| `matplotlib` | PSF-based (Matplotlib License) | Renders the dependency-map image |
| `pytest` | MIT | Tests |
| `ruff` | MIT | Lint |

## AI models used *by us* (not by Immich)

| Model | Provider | Role in the tool | Notes |
|---|---|---|---|
| `ministral-14b-latest` | Mistral AI (API) | **Planner** (decides the next experiment) | Verified usable on our key: 30 req/min |
| `ministral-8b-latest` | Mistral AI (API) | **Report writer** (findings from verified numbers) | Verified usable on our key: 188 req/min |
| Claude (Anthropic), via Claude Code | Anthropic | Development assistance: code, tests and docs were written with it | Disclose on the team form |

`mistral-small-latest` and `mistral-large-latest` were the first choice but are not usable on our
key (zero request quota / not in the subscription tier). See `docs/DECISIONS.md` D11. The tool also
runs with no LLM at all (deterministic planner and templated report).

## Data

| Dataset | Source | Notes |
|---|---|---|
| 100 sample photos | Supplied by the team lead | Uploaded to the local Immich sandbox only. Not committed to the repo (`data/` is git-ignored) |

## Our repo

| Item | Value |
|---|---|
| License | Apache-2.0 |
| Language | Python 3.11+ |
