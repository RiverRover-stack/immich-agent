"""Deterministic fallback planner: no LLM, no network, same answer every time.

priority(target) = 0.40 x (share of journeys depending on it)
                 + 0.25 x stateful
                 + 0.20 x single instance
                 + 0.15 x untested
(CLAUDE.md section 4.7.) It also produces dependency-implied predictions, so the prediction
scorecard works even without an LLM.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from shutterstorm.config import ROOT
from shutterstorm.schemas import Prediction, Proposal

DEFAULT_CATALOG = ROOT / "experiments" / "catalog.yaml"

WEIGHTS = {"dependents": 0.40, "stateful": 0.25, "single_instance": 0.20, "untested": 0.15}
# Used only if the catalog file is missing or silent about a target.
BUILTIN_CATALOG: dict[str, Any] = {
    "defaults": {"faults": ["stop", "pause", "cpu_throttle"]},
    "targets": {},
    "params": {"cpu_throttle": {"cpus": 0.5}},
    "ttl_s": {"stop": 30, "pause": 20, "cpu_throttle": 30},
}


def load_catalog(path: Path = DEFAULT_CATALOG) -> dict[str, Any]:
    if not path.exists():
        return BUILTIN_CATALOG
    data = yaml.safe_load(path.read_text()) or {}
    return {**BUILTIN_CATALOG, **data}


class FallbackPlanner:
    name = "fallback"

    def __init__(self, catalog: dict[str, Any] | None = None) -> None:
        self.catalog = catalog if catalog is not None else load_catalog()

    def _faults_for(self, target: str) -> list[str]:
        spec = (self.catalog.get("targets") or {}).get(target) or {}
        return list(spec.get("faults") or self.catalog["defaults"]["faults"])

    def plan(
        self,
        topology: dict[str, Any],
        results: list[dict[str, Any]] | None = None,
        baseline: dict[str, Any] | None = None,  # accepted for the Planner protocol; unused here
        allowed_faults: tuple[str, ...] | None = None,
        max_proposals: int = 12,
    ) -> list[Proposal]:
        results = results or []
        done = {(r["target"], r["fault"]) for r in results}
        tested_targets = {r["target"] for r in results}
        journeys = topology["journeys"]
        total = max(1, len(journeys))

        ranked: list[tuple[float, str, Proposal]] = []
        for node in topology["nodes"]:
            target = node["id"]
            candidates = [
                f
                for f in self._faults_for(target)
                if (target, f) not in done and (not allowed_faults or f in allowed_faults)
            ]
            fault = candidates[0] if candidates else None
            if fault is None:
                continue
            deps = [j["name"] for j in journeys if target in j["needs"]]
            stateful = node["kind"] == "stateful"
            single = node["replicas"] == 1
            untested = target not in tested_targets
            score = (
                WEIGHTS["dependents"] * len(deps) / total
                + WEIGHTS["stateful"] * stateful
                + WEIGHTS["single_instance"] * single
                + WEIGHTS["untested"] * untested
            )
            ranked.append((score, target, self._proposal(node, fault, deps, journeys, score)))

        ranked.sort(key=lambda r: (-r[0], r[1]))
        return [p for _, _, p in ranked[:max_proposals]]

    def _proposal(
        self,
        node: dict[str, Any],
        fault: str,
        deps: list[str],
        journeys: list[dict[str, Any]],
        score: float,
    ) -> Proposal:
        target = node["id"]
        broken = "fail" if fault in ("stop", "pause") else "degraded"
        preds = [
            Prediction(
                journey=j["name"],
                expected=broken if j["name"] in deps else "healthy",
                confidence=0.7 if j["name"] in deps else 0.8,
            )
            for j in journeys
        ]
        facts = [f"{len(deps)}/{len(journeys)} journeys depend on it"]
        if node["kind"] == "stateful":
            facts.append("stateful")
        if node["replicas"] == 1:
            facts.append("single instance")
        if not node["limits"].get("memory") and not node["limits"].get("cpus"):
            facts.append("no resource limits")
        params = dict((self.catalog.get("params") or {}).get(fault) or {})
        verb = {"stop": "crashes", "pause": "hangs", "cpu_throttle": "is starved of CPU"}[fault]
        return Proposal(
            target=target,
            fault=fault,
            params=params,
            ttl_s=float((self.catalog.get("ttl_s") or {}).get(fault, 30)),
            hypothesis=(
                f"When {target} {verb}, journeys that need it ({', '.join(deps) or 'none'}) "
                "degrade and unrelated journeys stay healthy."
            ),
            predictions=preds,
            protected_journeys=[j["name"] for j in journeys if j["name"] not in deps],
            reason=f"priority {score:.2f}: " + "; ".join(facts),
            planner=self.name,
        )
