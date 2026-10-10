"""The gate between any planner and the executor.

A proposal is never executed as written. This turns it into an ExperimentSpec with every value
clamped to a hard bound, or rejects it. Defence in depth: the executor re-checks the allowlist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from shutterstorm.chaos.faults import FaultError, get_fault
from shutterstorm.chaos.policy import IMMICH_POLICY, PolicyError, TargetPolicy
from shutterstorm.experiment import ExperimentSpec
from shutterstorm.guard.guard import GuardConfig
from shutterstorm.schemas import Proposal

MIN_TTL_S = 5.0
MAX_TTL_S = 120.0


class Rejected(Exception):
    """The proposal cannot be made safe."""


@dataclass
class Validated:
    spec: ExperimentSpec
    notes: list[str] = field(default_factory=list)  # every adjustment made, for the audit trail


def dependents_of(target: str, topology: dict[str, Any]) -> set[str]:
    """Journeys that need `target`, per the map."""
    return {j["name"] for j in topology["journeys"] if target in j["needs"]}


def validate(
    proposal: Proposal,
    topology: dict[str, Any],
    exp_id: str,
    policy: TargetPolicy = IMMICH_POLICY,
    baseline: dict[str, Any] | None = None,
) -> Validated:
    notes: list[str] = []
    try:
        target = policy.resolve(proposal.target)
        fault = get_fault(proposal.fault)
        params = fault.validate(dict(proposal.params))
    except (PolicyError, FaultError) as e:
        raise Rejected(str(e)) from e
    if params != {k: v for k, v in proposal.params.items() if k in params} and proposal.params:
        notes.append(f"params adjusted: {dict(proposal.params)} -> {params}")

    ttl = min(MAX_TTL_S, max(MIN_TTL_S, proposal.ttl_s))
    if ttl != proposal.ttl_s:
        notes.append(
            f"ttl_s adjusted: {proposal.ttl_s:g} -> {ttl:g} (allowed {MIN_TTL_S:g}-{MAX_TTL_S:g})"
        )

    known = {j["name"] for j in topology["journeys"]}
    depends = dependents_of(target, topology)

    # Protect only journeys that should survive. Protecting one that depends on the target would
    # make the guard abort on the very effect the experiment exists to observe.
    requested = proposal.protected_journeys or sorted(known - depends)
    protected = []
    for j in requested:
        if j not in known:
            notes.append(f"protected journey '{j}' unknown: dropped")
        elif j in depends:
            notes.append(f"protected journey '{j}' depends on {target}: dropped (expected to fail)")
        elif j == "upload_processing":
            notes.append("'upload_processing' is not a live probe: dropped from guard")
        else:
            protected.append(j)

    predictions = []
    for p in proposal.predictions:
        if p.journey in known:
            predictions.append(p.model_dump())
        else:
            notes.append(f"prediction for unknown journey '{p.journey}' dropped")

    guard = GuardConfig(
        protected=tuple(protected),
        baseline_p95_ms=_baseline_p95(baseline),
    )
    spec = ExperimentSpec(
        id=exp_id,
        target=target,
        fault=fault.name,
        params=params,
        ttl_s=ttl,
        protected=tuple(protected),
        hypothesis=proposal.hypothesis,
        reason=proposal.reason,
        predictions=predictions,
        planner=proposal.planner,
        guard=guard,
    )
    return Validated(spec=spec, notes=notes)


def _baseline_p95(baseline: dict[str, Any] | None) -> dict[str, float]:
    if not baseline:
        return {}
    return {
        j: s["p95_ms"]
        for j, s in baseline.get("journeys", {}).items()
        if s.get("p95_ms") is not None
    }
