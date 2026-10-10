import json

import httpx

from shutterstorm.llm.client import LLMClient, LLMConfig
from shutterstorm.planner.llm import LlmPlanner, build_facts
from shutterstorm.planner.validator import validate
from tests.test_planner import topo


def topology():
    t = topo()
    for n in t["nodes"]:
        n.setdefault("restart_policy", "always")
        n.setdefault("has_healthcheck", True)
        n.setdefault("depends_on", [])
    t["edges"] = [
        {
            "from": "immich_server",
            "to": "immich_machine_learning",
            "kind": "implicit",
            "evidence": ["not declared; search calls it"],
        }
    ]
    for j in t["journeys"]:
        j.setdefault("confidence", "high")
    t["audit"] = [
        {
            "id": "A-01",
            "severity": "high",
            "title": "Hidden dependency",
            "services": ["immich_server"],
        }
    ]
    return t


def planner_with(handler):
    cfg = LLMConfig("mistral", "https://x.test/v1", "k", "big", "small", min_interval_s=0.0)
    c = LLMClient(
        cfg, transport=httpx.MockTransport(handler), sleep=lambda s: None, clock=lambda: 0.0
    )
    return LlmPlanner(c)


def reply(proposals):
    return lambda req: httpx.Response(
        200,
        json={
            "model": "big",
            "choices": [{"message": {"content": json.dumps({"proposals": proposals})}}],
        },
    )


GOOD = {
    "target": "immich_machine_learning",
    "fault": "stop",
    "ttl_s": 30,
    "hypothesis": "uploads accepted but not indexed",
    "reason": "undeclared dependency",
    "predictions": [{"journey": "search", "expected": "fail", "confidence": 0.9}],
}


def test_llm_proposals_are_used_and_stamped():
    p = planner_with(reply([GOOD]))
    out = p.plan(topology())
    assert [(x.target, x.fault, x.planner) for x in out] == [
        ("immich_machine_learning", "stop", "llm")
    ]
    assert p.last_status == "llm"


def test_already_run_experiments_are_filtered_and_planner_falls_back():
    p = planner_with(reply([GOOD]))
    results = [{"target": "immich_machine_learning", "fault": "stop"}]
    out = p.plan(topology(), results)
    assert out and all(x.planner == "fallback" for x in out)
    assert "nothing new" in p.last_status


def test_api_failure_falls_back_and_says_so():
    p = planner_with(lambda req: httpx.Response(503, text="down"))
    out = p.plan(topology())
    assert out and out[0].planner == "fallback"
    assert p.last_status.startswith("fallback")
    assert p.history[-1] == p.last_status


def test_garbage_output_falls_back():
    p = planner_with(
        lambda req: httpx.Response(
            200, json={"choices": [{"message": {"content": "sure! kaboom"}}]}
        )
    )
    assert p.plan(topology())[0].planner == "fallback"


def test_llm_cannot_smuggle_an_unsafe_proposal_past_the_validator():
    evil = {**GOOD, "target": "host_database", "fault": "rm_rf", "ttl_s": 99999}
    p = planner_with(reply([evil]))
    out = p.plan(topology())  # schema-valid, so the planner returns it...
    assert out[0].target == "host_database"
    import pytest

    from shutterstorm.planner.validator import Rejected

    with pytest.raises(Rejected):  # ...and the validator is what stops it
        validate(out[0], topology(), "E-01")


def test_allowed_faults_restriction_is_enforced_on_llm_output():
    pause = {**GOOD, "fault": "pause"}
    p = planner_with(reply([pause, GOOD]))
    out = p.plan(topology(), allowed_faults=("stop",))
    assert [x.fault for x in out] == ["stop"]


def test_facts_brief_contains_no_secrets_or_raw_logs():
    t = topology()
    t["nodes"][0]["volumes"] = ["/home/user/.ssh:/x"]
    t["nodes"][0]["env_secret"] = "hunter2"
    facts = json.dumps(build_facts(t, [], None, None, {"faults": ["stop"], "params": {}}))
    assert "hunter2" not in facts and ".ssh" not in facts
    assert "immich_machine_learning" in facts and "A-01" in facts


def test_brief_tells_the_model_which_fault_types_were_already_used():
    results = [
        {"target": "immich_redis", "fault": "stop", "journeys": {}, "experiment_id": "E-01"},
        {"target": "immich_postgres", "fault": "pause", "journeys": {}, "experiment_id": "E-02"},
    ]
    facts = build_facts(topology(), results, None, None, {"faults": ["stop"], "params": {}})
    assert facts["fault_types_used"] == ["pause", "stop"]
    from shutterstorm.planner.llm import SYSTEM

    assert "fault_types_used" in SYSTEM and "cpu_throttle" in SYSTEM
