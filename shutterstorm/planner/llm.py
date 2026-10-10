"""LLM planner: the large model decides what to break next. Code constrains and verifies it.

Input is a compact facts-only brief (topology, journeys, audit, baseline, results so far).
Raw container logs are never sent. Output is schema-validated into Proposals; the validator
and executor then clamp and gate every value. Any failure falls back to the formula planner,
and `last_status` records which one actually decided (reported honestly in the findings).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from shutterstorm.llm.client import LLMClient, LLMError
from shutterstorm.planner.fallback import FallbackPlanner
from shutterstorm.planner.validator import MAX_TTL_S, MIN_TTL_S
from shutterstorm.schemas import Proposal

MAX_PROPOSALS = 3

SYSTEM = f"""You are the experiment planner of a chaos-engineering tool testing a self-hosted photo app
(Immich) inside the operator's own local sandbox. Decide which single failure experiment is the
most informative to run next.

How to decide:
- Find the component whose failure would teach us the most about hidden weaknesses. Prefer
  dependencies the compose file does NOT declare, stateful single instances, and anything not yet
  tested. Use the static audit and the results so far.
- Never repeat an experiment listed under experiments_done. Learn from their results:
  e.g. if accepted uploads were not processed, probe the components on that path next.
- Cover different failure types across the session: a crash (stop), a hang (pause) and resource
  starvation (cpu_throttle) reveal different weaknesses. When two experiments are about equally
  informative, prefer a fault type that has not been run yet (see fault_types_used). A hang can be
  worse than a crash, because nothing restarts a process that is alive but unresponsive.
- ttl_s of 20-40 is enough to observe an effect; do not use longer than needed.
- Predict, per journey, whether it will be healthy, degraded or fail, with a confidence 0-1.
  Only journeys whose `needs` include the target should be predicted to break.
- protected_journeys are journeys that must NOT be affected; a guard aborts if they are.
- Quote concrete facts in `reason` (numbers and component names from the brief). Do not invent
  figures.

Hard limits (anything outside is rejected by code): fault in the allowed list; target one of the
listed components; ttl_s between {MIN_TTL_S:g} and {MAX_TTL_S:g}; cpu_throttle cpus >= 0.25.
Return up to {MAX_PROPOSALS} proposals ranked best first. Leave `planner` unset."""


class PlanResponse(BaseModel):
    proposals: list[Proposal] = Field(min_length=1, max_length=5)


def build_facts(
    topology: dict[str, Any],
    results: list[dict[str, Any]],
    baseline: dict[str, Any] | None,
    allowed_faults: tuple[str, ...] | None,
    fault_catalog: dict[str, Any],
) -> dict[str, Any]:
    def trim(s: str, n: int = 220) -> str:
        return s if len(s) <= n else s[:n] + "…"

    done = []
    for r in results:
        done.append(
            {
                "id": r.get("experiment_id"),
                "target": r["target"],
                "fault": r["fault"],
                "observed": {j: v["observed"] for j, v in r.get("journeys", {}).items()},
                "recovery_s": r.get("recovery_s"),
                "uploads_accepted_during_fault": r.get("async", {}).get("accepted_during_fault"),
                "uploads_never_indexed": r.get("async", {}).get("never_indexed_during_fault"),
                "unexpected_collateral": r.get("journeys_hit_unexpectedly"),
                "prediction_accuracy": (r.get("predictions") or {}).get("accuracy"),
                "outcome": r.get("outcome"),
            }
        )
    return {
        "components": [
            {
                "id": n["id"],
                "kind": n["kind"],
                "replicas": n["replicas"],
                "restart_policy": n["restart_policy"],
                "has_healthcheck": n["has_healthcheck"],
                "resource_limits_set": bool(n["limits"].get("memory") or n["limits"].get("cpus")),
                "declared_depends_on": n["depends_on"],
            }
            for n in topology["nodes"]
        ],
        "edges": [
            {
                "from": e["from"],
                "to": e["to"],
                "kind": e["kind"],
                "evidence": trim(e["evidence"][0]),
            }
            for e in topology["edges"]
        ],
        "journeys": [
            {"name": j["name"], "needs": j["needs"], "confidence": j["confidence"]}
            for j in topology["journeys"]
        ],
        "static_audit": [
            {
                "id": a["id"],
                "severity": a["severity"],
                "title": a["title"],
                "services": a["services"],
            }
            for a in topology.get("audit", [])
        ],
        "baseline": {
            "journeys": {
                j: {"p95_ms": s["p95_ms"], "error_rate": s["error_rate"]}
                for j, s in (baseline or {}).get("journeys", {}).items()
            },
            "uploads": (baseline or {}).get("uploads"),
        },
        "experiments_done": done,
        "fault_types_used": sorted({r["fault"] for r in results}),
        "allowed_faults": list(allowed_faults or fault_catalog.get("faults", [])),
        "fault_params": fault_catalog.get("params", {}),
    }


class LlmPlanner:
    name = "llm"

    def __init__(
        self,
        client: LLMClient,
        fallback: FallbackPlanner | None = None,
    ) -> None:
        self.client = client
        self.fallback = fallback or FallbackPlanner()
        self.last_status = "not called"
        self.history: list[str] = []  # one line per call, for the report's provenance table

    def plan(
        self,
        topology: dict[str, Any],
        results: list[dict[str, Any]] | None = None,
        baseline: dict[str, Any] | None = None,
        allowed_faults: tuple[str, ...] | None = None,
    ) -> list[Proposal]:
        results = results or []
        done = {(r["target"], r["fault"]) for r in results}
        catalog = {
            "faults": ["stop", "pause", "cpu_throttle"],
            "params": {"cpu_throttle": {"cpus": "0.25 or more"}},
        }
        facts = build_facts(topology, results, baseline, allowed_faults, catalog)
        try:
            resp = self.client.complete_json(
                "large",
                SYSTEM,
                "Facts (JSON):\n" + json.dumps(facts, indent=1),
                PlanResponse,
                max_tokens=1800,
                temperature=0.3,
            )
        except LLMError as e:
            return self._fell_back(topology, results, baseline, allowed_faults, str(e))

        usable = []
        for p in resp.proposals:
            if (p.target, p.fault) in done:
                continue
            if allowed_faults and p.fault not in allowed_faults:
                continue
            usable.append(p.model_copy(update={"planner": "llm"}))
        if not usable:
            return self._fell_back(
                topology, results, baseline, allowed_faults, "model proposed nothing new/allowed"
            )
        self.last_status = "llm"
        self.history.append(f"llm ({self.client.model_for('large')}): {len(usable)} proposal(s)")
        return usable

    def _fell_back(
        self,
        topology: dict[str, Any],
        results: list[dict[str, Any]],
        baseline: dict[str, Any] | None,
        allowed_faults: tuple[str, ...] | None,
        why: str,
    ) -> list[Proposal]:
        self.last_status = f"fallback ({why})"
        self.history.append(self.last_status)
        return self.fallback.plan(topology, results, baseline, allowed_faults=allowed_faults)
