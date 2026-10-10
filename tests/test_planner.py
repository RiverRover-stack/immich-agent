import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from shutterstorm.planner.fallback import FallbackPlanner
from shutterstorm.planner.validator import MAX_TTL_S, MIN_TTL_S, Rejected, validate
from shutterstorm.schemas import Prediction, Proposal


def topo():
    nodes = [
        {"id": "immich_server", "kind": "stateful", "replicas": 1, "limits": {}},
        {"id": "immich_machine_learning", "kind": "stateless", "replicas": 1, "limits": {}},
        {"id": "immich_postgres", "kind": "stateful", "replicas": 1, "limits": {}},
        {"id": "immich_redis", "kind": "stateful", "replicas": 1, "limits": {}},
    ]
    s, ml, pg, rd = (n["id"] for n in nodes)
    journeys = [
        {"name": "ping", "needs": [s]},
        {"name": "browse", "needs": [s, pg]},
        {"name": "search", "needs": [s, ml, pg]},
        {"name": "upload", "needs": [s, pg]},
        {"name": "upload_processing", "needs": [s, pg, rd, ml]},
    ]
    return {"nodes": nodes, "journeys": journeys}


def test_most_depended_on_stateful_component_is_ranked_first():
    plan = FallbackPlanner().plan(topo())
    assert plan[0].target == "immich_server"  # 5/5 journeys depend on it
    # By the specified formula: server 1.00, postgres 0.92, redis 0.68, ML 0.51.
    # Note ML ranks LAST although it is the hidden dependency: the formula counts stateful and
    # dependents, not "undeclared". That is a known limitation the LLM planner is meant to fix.
    assert [p.target for p in plan] == [
        "immich_server",
        "immich_postgres",
        "immich_redis",
        "immich_machine_learning",
    ]


def test_planner_is_deterministic():
    a = FallbackPlanner().plan(topo())
    b = FallbackPlanner().plan(topo())
    assert [p.model_dump() for p in a] == [p.model_dump() for p in b]


def test_untested_targets_rise_and_exhausted_faults_move_on():
    results = [{"target": "immich_server", "fault": "stop"}]
    plan = FallbackPlanner().plan(topo(), results)
    assert plan[0].target != "immich_server"  # tested one drops below untested ones
    server = [p for p in plan if p.target == "immich_server"]
    # catalog order for the server is cpu_throttle, stop, pause; stop is done so cpu_throttle is next
    assert server and server[0].fault == "cpu_throttle"

    everything = [
        {"target": n["id"], "fault": f}
        for n in topo()["nodes"]
        for f in ("stop", "pause", "cpu_throttle")
    ]
    assert FallbackPlanner().plan(topo(), everything) == []


def test_predictions_follow_the_dependency_map():
    p = next(p for p in FallbackPlanner().plan(topo()) if p.target == "immich_machine_learning")
    got = {x.journey: x.expected for x in p.predictions}
    assert got == {
        "ping": "healthy",
        "browse": "healthy",
        "upload": "healthy",  # acceptance does not need ML
        "search": "fail",
        "upload_processing": "fail",
    }
    assert "ping" in p.protected_journeys and "search" not in p.protected_journeys


def test_validator_accepts_and_resolves_aliases():
    v = validate(Proposal(target="ml", fault="stop", ttl_s=30), topo(), "E-01")
    assert v.spec.target == "immich_machine_learning"
    assert v.spec.fault == "stop"


@pytest.mark.parametrize(
    "target,fault",
    [("nginx", "stop"), ("immich_server", "rm_rf"), ("../../etc", "stop"), ("", "stop")],
)
def test_validator_rejects_unknown_targets_and_faults(target, fault):
    with pytest.raises(Rejected):
        validate(Proposal(target=target, fault=fault), topo(), "E-X")


def test_validator_clamps_ttl_and_cpu_and_records_it():
    big = validate(
        Proposal(target="db", fault="cpu_throttle", ttl_s=9999, params={"cpus": 0.01}), topo(), "E"
    )
    assert big.spec.ttl_s == MAX_TTL_S
    assert big.spec.params["cpus"] == 0.25
    assert any("ttl_s adjusted" in n for n in big.notes)
    tiny = validate(Proposal(target="db", fault="stop", ttl_s=0.1), topo(), "E")
    assert tiny.spec.ttl_s == MIN_TTL_S


def test_validator_will_not_protect_a_journey_that_must_fail():
    p = Proposal(target="ml", fault="stop", protected_journeys=["ping", "search", "ghost"])
    v = validate(p, topo(), "E-01")
    assert v.spec.protected == ("ping",)
    assert any("search" in n and "dropped" in n for n in v.notes)
    assert any("ghost" in n for n in v.notes)


def test_validator_drops_predictions_for_unknown_journeys():
    p = Proposal(
        target="ml",
        fault="stop",
        predictions=[
            Prediction(journey="search", expected="fail"),
            Prediction(journey="nope", expected="fail"),
        ],
    )
    v = validate(p, topo(), "E")
    assert [x["journey"] for x in v.spec.predictions] == ["search"]


def test_schema_rejects_malformed_llm_output():
    with pytest.raises(ValidationError):
        Proposal.model_validate({"target": "ml", "fault": "stop", "ttl_s": -5})
    with pytest.raises(ValidationError):
        Proposal.model_validate_json(
            json.dumps(
                {
                    "target": "ml",
                    "fault": "stop",
                    "predictions": [{"journey": "x", "expected": "kaboom"}],
                }
            )
        )


def test_real_topology_produces_valid_experiments_for_every_proposal():
    path = Path(__file__).resolve().parent.parent / "reports" / "topology.json"
    if not path.exists():
        pytest.skip("run `shutterstorm discover` first")
    topology = json.loads(path.read_text())
    plan = FallbackPlanner().plan(topology)
    assert plan
    for i, p in enumerate(plan):
        validate(p, topology, f"E-{i}")  # must never raise


def test_catalog_drives_which_fault_runs_first_per_target():
    plan = {p.target: p.fault for p in FallbackPlanner().plan(topo())}
    assert plan["immich_machine_learning"] == "stop"
    assert plan["immich_postgres"] == "pause"
    assert plan["immich_redis"] == "stop"
    assert plan["immich_server"] == "cpu_throttle"
    assert len(set(plan.values())) >= 3  # three failure types in one session (Day-2 minimum)


def test_custom_catalog_is_honoured():
    cat = {"defaults": {"faults": ["pause"]}, "targets": {}, "params": {}, "ttl_s": {"pause": 7}}
    plan = FallbackPlanner(catalog=cat).plan(topo())
    assert {p.fault for p in plan} == {"pause"}
    assert plan[0].ttl_s == 7.0
