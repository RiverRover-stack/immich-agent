import json

import httpx
import pytest

from shutterstorm.analyzer.metrics import analyze_experiment
from shutterstorm.llm.client import LLMClient, LLMConfig
from shutterstorm.report.findings import (
    Finding,
    allowed_numbers,
    build_evidence,
    template_findings,
    unverified_numbers,
    write_findings,
)
from shutterstorm.report.render import build_report_data, render
from tests.test_analyzer import make_run

TOPO = {
    "audit": [
        {
            "id": "A-01",
            "severity": "high",
            "title": "Hidden dependency: a needs b but does not declare it",
            "services": ["immich_server", "immich_machine_learning"],
        },
        {
            "id": "A-02",
            "severity": "medium",
            "title": "No CPU or memory limits: one container can starve the others",
            "services": ["immich_server"],
        },
    ]
}


@pytest.fixture
def result(tmp_path):
    r = analyze_experiment(make_run(tmp_path))
    r["experiment_id"] = "E-01"
    r["target"] = "immich_machine_learning"
    r["planner"] = "fallback"
    r["planner_reason"] = "priority 0.51: 2/5 journeys depend on it"
    return r


@pytest.fixture
def evidence(result):
    return build_evidence([result], TOPO)


def client_returning(content: str | list[str]):
    replies = [content] if isinstance(content, str) else list(content)

    def handler(req):
        text = replies.pop(0) if len(replies) > 1 else replies[0]
        return httpx.Response(
            200, json={"model": "small", "choices": [{"message": {"content": text}}]}
        )

    cfg = LLMConfig("mistral", "https://x.test/v1", "k", "big", "small", min_interval_s=0.0)
    return LLMClient(
        cfg, transport=httpx.MockTransport(handler), sleep=lambda s: None, clock=lambda: 0.0
    )


def rewrite(findings, **override):
    out = []
    for f in findings:
        d = f.model_dump()
        d.update(override)
        out.append(d)
    return json.dumps({"findings": out})


# ---- templates ---------------------------------------------------------
def test_templates_find_the_silent_processing_loss_and_rank_by_severity(evidence):
    fs = template_findings(evidence)
    titles = [f.title for f in fs]
    assert any("never fully processed" in t for t in titles)
    assert [f.id for f in fs] == [f"F-{i:02d}" for i in range(1, len(fs) + 1)]
    sev = [f.severity for f in fs]
    assert sev == sorted(sev, key=["critical", "high", "medium", "low"].index)
    static = [f for f in fs if f.source == "static"]
    assert {f.evidence[0] for f in static} == {"A-01", "A-02"}
    assert all(f.source == "experiment" for f in fs if f.evidence[0].startswith("E-"))


def test_every_template_number_is_traceable_to_the_evidence(evidence):
    allowed = allowed_numbers(evidence)
    for f in template_findings(evidence):
        for field in (f.title, f.cause, f.impact, f.fix):
            assert unverified_numbers(field, allowed) == [], (f.title, field)


# ---- number checker ----------------------------------------------------
def test_checker_accepts_rates_as_percent_and_rejects_invented_numbers():
    allowed = allowed_numbers({"error_rate": 0.333, "recovery_s": 14.0, "n": 7})
    assert unverified_numbers("error rate 33% and recovery 14 s over 7 uploads", allowed) == []
    assert unverified_numbers("recovery took 90 seconds", allowed) == [90.0]
    assert unverified_numbers("a 12x slowdown", allowed) == [12.0]


def test_checker_ignores_identifiers_and_versions():
    allowed = allowed_numbers({"x": 5})
    assert unverified_numbers("see E-07, A-12 and Immich v3.3.1 / 3.3.1", allowed) == []


# ---- LLM writer --------------------------------------------------------
def test_no_client_uses_templates(evidence):
    fs, prov = write_findings(evidence, None)
    assert prov["writer"] == "template" and fs


def test_verified_rewrite_is_accepted_and_marked_llm(evidence):
    drafts = template_findings(evidence)
    fs, prov = write_findings(evidence, client_returning(rewrite(drafts)))
    assert prov["writer"] == "llm" and prov["model"] == "small"
    assert all(f.written_by == "llm" for f in fs)


def test_invented_number_is_rejected_and_templates_are_used(evidence):
    drafts = template_findings(evidence)
    lies = rewrite(drafts, cause="Recovery took 4821 seconds and 977 requests failed.")
    fs, prov = write_findings(evidence, client_returning(lies))
    assert prov["writer"] == "template"
    assert prov["rejected"] and "unverified numbers" in prov["rejected"][0]["problems"][0]
    assert all("4821" not in f.cause for f in fs)


def test_second_attempt_can_succeed_after_a_rejection(evidence):
    drafts = template_findings(evidence)
    bad = rewrite(drafts, cause="It took 4821 seconds.")
    good = rewrite(drafts)
    _fs, prov = write_findings(evidence, client_returning([bad, good]))
    assert prov["writer"] == "llm" and len(prov["rejected"]) == 1


def test_severity_inflation_and_dropped_findings_and_fake_evidence_are_rejected(evidence):
    drafts = template_findings(evidence)
    inflated = rewrite(drafts, severity="critical")
    assert write_findings(evidence, client_returning(inflated))[1]["writer"] == "template"

    fake_ref = rewrite(drafts, evidence=["E-99"])
    assert write_findings(evidence, client_returning(fake_ref))[1]["writer"] == "template"

    dropped = json.dumps({"findings": [drafts[0].model_dump()]})
    assert write_findings(evidence, client_returning(dropped))[1]["writer"] == "template"


def test_llm_outage_degrades_to_templates(evidence):
    cfg = LLMConfig("mistral", "https://x.test/v1", "k", "big", "small", min_interval_s=0.0)
    c = LLMClient(
        cfg,
        transport=httpx.MockTransport(lambda r: httpx.Response(403, json={})),
        sleep=lambda s: None,
        clock=lambda: 0.0,
    )
    fs, prov = write_findings(evidence, c)
    assert prov["writer"] == "template" and "unavailable" in prov["note"] and fs


# ---- rendering ---------------------------------------------------------
def test_markdown_and_html_contain_the_key_sections_and_numbers(result, tmp_path):
    data = build_report_data([result], TOPO, {"duration_s": 294.3}, client=None)
    md_path, html_path = render(data, tmp_path / "out")
    md, html = md_path.read_text(), html_path.read_text()
    for needle in (
        "Ranked findings",
        "Resilience ranking",
        "Safety record",
        "E-01",
        "never fully processed",
        "n = 1",
        "prediction accuracy",
    ):
        assert needle in md, needle
    assert "<h1>ShutterStorm findings report</h1>" in html
    assert "never indexed" in html


def test_html_escapes_untrusted_text(result, tmp_path):
    result["hypothesis"] = "<script>alert('x')</script>"
    data = build_report_data([result], TOPO, None, client=None)
    _, html_path = render(data, tmp_path / "out")
    html = html_path.read_text()
    assert "<script>alert" not in html and "&lt;script&gt;" in html


def test_finding_schema_requires_evidence():
    with pytest.raises(ValueError):
        Finding(title="t", severity="high", evidence=[], cause="c", impact="i", fix="f")


def test_findings_sharing_evidence_keep_their_own_severities(evidence):
    drafts = template_findings(evidence)
    shared = [f for f in drafts if f.evidence == ["E-01"]]
    assert len({f.severity for f in shared}) > 1  # the situation that used to be mis-verified
    _fs, prov = write_findings(evidence, client_returning(rewrite(drafts)))
    assert prov["writer"] == "llm" and prov["rejected"] == []


def test_overclaims_are_rejected_even_when_every_number_is_correct(evidence):
    drafts = template_findings(evidence)
    lie = rewrite(drafts, title="Silent data loss when ML is down")
    _fs, prov = write_findings(evidence, client_returning(lie))
    assert prov["writer"] == "template"
    assert "not supported" in prov["rejected"][0]["problems"][0]

    cascade = rewrite(drafts, impact="Failures cascade unpredictably across services.")
    assert write_findings(evidence, client_returning(cascade))[1]["writer"] == "template"


def test_cascade_wording_is_allowed_only_when_collateral_damage_was_measured(evidence):
    evidence["experiments"][0]["hit_unexpectedly"] = ["browse"]
    drafts = template_findings(evidence)
    ok = rewrite(
        drafts, impact="The failure cascades to browse, which is not mapped to this service."
    )
    assert write_findings(evidence, client_returning(ok))[1]["writer"] == "llm"


def test_templates_themselves_contain_no_overclaims(evidence):
    from shutterstorm.report.findings import overclaims

    for f in template_findings(evidence):
        assert overclaims(f, evidence) == [], f.title
