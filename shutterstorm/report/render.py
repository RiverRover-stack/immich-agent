"""Assemble report data and render it as Markdown and a single self-contained HTML file."""

from __future__ import annotations

import base64
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, select_autoescape

from shutterstorm.report.findings import Finding, build_evidence, write_findings


def _short(name: str) -> str:
    return name.replace("immich_", "")


def build_report_data(
    results: list[dict[str, Any]],
    topology: dict[str, Any],
    baseline: dict[str, Any] | None,
    client: Any = None,
    session: dict[str, Any] | None = None,
    planner_history: list[str] | None = None,
) -> dict[str, Any]:
    evidence = build_evidence(results, topology)
    findings, prov = write_findings(evidence, client)

    scores = [r["score"]["total"] for r in results]
    ranked = sorted(results, key=lambda r: r["score"]["total"])
    accs = [
        r["predictions"]["accuracy"] for r in results if r["predictions"]["accuracy"] is not None
    ]
    per_exp = []
    for r in results:
        per_exp.append(
            {
                **r,
                "target_short": _short(r["target"]),
                "journey_rows": [
                    {
                        "journey": j,
                        "pre": v.get("pre"),
                        "during": v.get("during"),
                        "after": v.get("after"),
                        "observed": v["observed"],
                        "expected": next(
                            (p["expected"] for p in r["predictions"]["items"] if p["journey"] == j),
                            None,
                        ),
                    }
                    for j, v in sorted(r["journeys"].items())
                ],
            }
        )
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "immich_version": "v3.3.1",
        "findings": findings,
        "provenance": prov,
        "experiments": per_exp,
        "ranking": [
            {
                "rank": i,
                "id": r["experiment_id"],
                "target": _short(r["target"]),
                "fault": r["fault"],
                "score": r["score"]["total"],
            }
            for i, r in enumerate(ranked, 1)
        ],
        "overall_score": round(statistics.mean(scores), 1) if scores else None,
        "prediction_accuracy": round(statistics.mean(accs), 3) if accs else None,
        "audit": topology.get("audit", []),
        "topology": topology,
        "baseline": baseline,
        "session": session or {},
        "planners": sorted({r.get("planner") or "manual" for r in results}),
        "planner_history": planner_history or [],
        "models": sorted({c.model for c in getattr(client, "calls", [])}) if client else [],
        "llm_usage": client.usage() if client is not None and hasattr(client, "usage") else None,
    }


MD = """# ShutterStorm findings report

Generated {{ d.generated_at }} · target: Immich {{ d.immich_version }} (local sandbox) ·
experiments: {{ d.experiments|length }} · overall resilience score: **{{ d.overall_score }} / 100**

> Every number below was computed by code from recorded probe data.
> Findings text: **{{ d.provenance.writer }}**{% if d.provenance.model %} ({{ d.provenance.model }}){% endif %}
> — {{ d.provenance.note }}.
> Experiments chosen by: {{ d.planners|join(", ") }}.
{%- if d.session.stopped_because %} Session ended: {{ d.session.stopped_because }}.{% endif %}

## 1. Ranked findings

{% for f in d.findings -%}
### {{ f.id }} · {{ f.severity|upper }} · {{ f.title }}

- **Evidence:** {{ f.evidence|join(", ") }}{% if f.source == "static" %} (static configuration, not an experiment){% endif %}

- **Cause:** {{ f.cause }}
- **Impact:** {{ f.impact }}
- **Fix:** {{ f.fix }}

{% endfor %}
## 2. Resilience ranking (weakest first)

| # | Experiment | Target | Fault | Score |
|---|---|---|---|---|
{% for r in d.ranking -%}
| {{ r.rank }} | {{ r.id }} | {{ r.target }} | {{ r.fault }} | {{ r.score }} |
{% endfor %}
Score = 100 × (0.35·availability + 0.20·recovery + 0.20·async completion + 0.15·integrity
+ 0.10·blast radius). It is a documented heuristic, comparable between experiments here, not an
industry standard.

## 3. Experiments

{% for e in d.experiments -%}
### {{ e.experiment_id }} · {{ e.fault }} → {{ e.target_short }} · score {{ e.score.total }}

- Chosen by: **{{ e.planner }}**{% if e.planner_reason %} — {{ e.planner_reason }}{% endif %}

- Hypothesis: {{ e.hypothesis }}
- Outcome: **{{ e.outcome }}**{% if e.abort_reason %} ({{ e.abort_reason }}){% endif %} ·
  fault held {{ e.fault_duration_s }} s · undo verified: {{ e.undone_verified }} ·
  recovery: {{ e.recovery_s if e.recovered else "not recovered" }}{% if e.recovered %} s{% endif %}
  · back to baseline: {{ e.after.all_back_to_baseline }}
- Uploads during fault: {{ e.async.accepted_during_fault }} accepted,
  {{ e.async.thumbnail_ok_during_fault }} thumbnailed, {{ e.async.indexed_ok_during_fault }} indexed,
  {{ e.async.never_indexed_during_fault }} never indexed

| Journey | Predicted | Observed | Before err / p95 | During err / p95 | After err |
|---|---|---|---|---|---|
{% for j in e.journey_rows -%}
| {{ j.journey }} | {{ j.expected or "—" }} | {{ j.observed }} |
{{- " " }}{{ fmt(j.pre) }} | {{ fmt(j.during) }} | {{ fmt_err(j.after) }} |
{% endfor %}
{% if e.predictions.accuracy is not none %}Prediction accuracy: {{ (e.predictions.accuracy*100)|round|int }}%.{% endif %}

{% endfor %}
## 4. Static configuration audit

| ID | Severity | Services | Finding |
|---|---|---|---|
{% for a in d.audit -%}
| {{ a.id }} | {{ a.severity }} | {{ a.services|map("replace", "immich_", "")|join(", ") }} | {{ a.title }} |
{% endfor %}
## 5. Safety record

| Experiment | Guard tripped | Undo verified | Fault held (s) |
|---|---|---|---|
{% for e in d.experiments -%}
| {{ e.experiment_id }} | {{ "yes: " ~ e.abort_reason if e.outcome == "aborted_guard" else "no" }} | {{ e.undone_verified }} | {{ e.fault_duration_s }} |
{% endfor %}
## 6. Method and limits

- Steady-state baseline: {{ d.baseline.duration_s if d.baseline else "n/a" }} s of probes before any fault.
- One run per experiment (n = 1). Results show what happened here, not a statistical distribution.
- Single machine, CPU-only inference, 100 synthetic-size sample photos; absolute latencies will differ elsewhere.
- Mean prediction accuracy: {{ d.prediction_accuracy if d.prediction_accuracy is not none else "n/a" }}.
{% if d.llm_usage -%}
- LLM usage in this run: {{ d.llm_usage.calls }} calls, {{ d.llm_usage.prompt_tokens }} prompt +
  {{ d.llm_usage.completion_tokens }} completion tokens.
{% endif -%}
{% for line in d.planner_history -%}
- Planner call: {{ line }}
{% endfor %}
"""

HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>ShutterStorm findings report</title>
<style>
body{font:15px/1.5 system-ui,sans-serif;max-width:980px;margin:2rem auto;padding:0 1rem;color:#1b1f24}
h1,h2{border-bottom:1px solid #d0d7de;padding-bottom:.3rem} table{border-collapse:collapse;width:100%;margin:.6rem 0}
th,td{border:1px solid #d0d7de;padding:.35rem .5rem;text-align:left;font-size:13px} th{background:#f6f8fa}
.sev{display:inline-block;padding:.05rem .5rem;border-radius:1rem;color:#fff;font-size:12px;font-weight:600}
.critical{background:#8e1b1b}.high{background:#cf222e}.medium{background:#bf8700}.low{background:#6e7781}
.card{border:1px solid #d0d7de;border-radius:8px;padding:.8rem 1rem;margin:.8rem 0}
.score{font-size:2rem;font-weight:700}.muted{color:#57606a;font-size:13px}
.fail{color:#cf222e;font-weight:600}.healthy{color:#1a7f37}.degraded{color:#9a6700;font-weight:600}
img{max-width:100%}
</style></head><body>
<h1>ShutterStorm findings report</h1>
<p class="muted">Generated {{ d.generated_at }} · target Immich {{ d.immich_version }} (local sandbox) ·
{{ d.experiments|length }} experiments · findings text: <b>{{ d.provenance.writer }}</b>
{% if d.provenance.model %}({{ d.provenance.model }}){% endif %} — {{ d.provenance.note }} ·
experiments chosen by: {{ d.planners|join(", ") }}</p>
<p><span class="score">{{ d.overall_score }}</span> / 100 overall resilience score
<span class="muted">(heuristic; weakest experiment first below)</span></p>
{% if map_png %}<h2>Dependency map</h2><img alt="dependency map" src="data:image/png;base64,{{ map_png }}">{% endif %}
<h2>1. Ranked findings</h2>
{% for f in d.findings %}<div class="card">
<b>{{ f.id }}</b> <span class="sev {{ f.severity }}">{{ f.severity }}</span> <b>{{ f.title }}</b>
<div class="muted">Evidence: {{ f.evidence|join(", ") }}{% if f.source == "static" %} · static configuration{% endif %}</div>
<p><b>Cause.</b> {{ f.cause }}<br><b>Impact.</b> {{ f.impact }}<br><b>Fix.</b> {{ f.fix }}</p></div>{% endfor %}
<h2>2. Resilience ranking (weakest first)</h2>
<table><tr><th>#</th><th>Experiment</th><th>Target</th><th>Fault</th><th>Score</th></tr>
{% for r in d.ranking %}<tr><td>{{ r.rank }}</td><td>{{ r.id }}</td><td>{{ r.target }}</td><td>{{ r.fault }}</td><td>{{ r.score }}</td></tr>{% endfor %}</table>
<h2>3. Experiments</h2>
{% for e in d.experiments %}<div class="card"><b>{{ e.experiment_id }} · {{ e.fault }} → {{ e.target_short }}</b>
· score <b>{{ e.score.total }}</b>
<div class="muted">Chosen by {{ e.planner }}{% if e.planner_reason %} — {{ e.planner_reason }}{% endif %}</div>
<p>{{ e.hypothesis }}<br>Outcome <b>{{ e.outcome }}</b>{% if e.abort_reason %} ({{ e.abort_reason }}){% endif %} ·
held {{ e.fault_duration_s }} s · undo verified {{ e.undone_verified }} ·
recovery {{ e.recovery_s if e.recovered else "not recovered" }}{% if e.recovered %} s{% endif %} ·
back to baseline {{ e.after.all_back_to_baseline }}<br>
Uploads during fault: {{ e.async.accepted_during_fault }} accepted, {{ e.async.thumbnail_ok_during_fault }} thumbnailed,
{{ e.async.indexed_ok_during_fault }} indexed, <b>{{ e.async.never_indexed_during_fault }} never indexed</b></p>
<table><tr><th>Journey</th><th>Predicted</th><th>Observed</th><th>Before err / p95</th><th>During err / p95</th><th>After err</th></tr>
{% for j in e.journey_rows %}<tr><td>{{ j.journey }}</td><td>{{ j.expected or "—" }}</td>
<td class="{{ j.observed }}">{{ j.observed }}</td><td>{{ fmt(j.pre) }}</td><td>{{ fmt(j.during) }}</td><td>{{ fmt_err(j.after) }}</td></tr>{% endfor %}</table>
</div>{% endfor %}
<h2>4. Static configuration audit</h2>
<table><tr><th>ID</th><th>Severity</th><th>Services</th><th>Finding</th></tr>
{% for a in d.audit %}<tr><td>{{ a.id }}</td><td>{{ a.severity }}</td><td>{{ a.services|map("replace","immich_","")|join(", ") }}</td><td>{{ a.title }}</td></tr>{% endfor %}</table>
<h2>5. Safety record</h2>
<table><tr><th>Experiment</th><th>Guard tripped</th><th>Undo verified</th><th>Fault held (s)</th></tr>
{% for e in d.experiments %}<tr><td>{{ e.experiment_id }}</td><td>{{ "yes: " ~ e.abort_reason if e.outcome == "aborted_guard" else "no" }}</td><td>{{ e.undone_verified }}</td><td>{{ e.fault_duration_s }}</td></tr>{% endfor %}</table>
<h2>6. Method and limits</h2>
<ul><li>Steady-state baseline: {{ d.baseline.duration_s if d.baseline else "n/a" }} s before any fault.</li>
<li>One run per experiment (n = 1): results show what happened here, not a distribution.</li>
<li>Single machine, CPU-only inference; absolute latencies will differ elsewhere.</li>
<li>Mean prediction accuracy: {{ d.prediction_accuracy if d.prediction_accuracy is not none else "n/a" }}.</li>
<li>Every number was computed by code from recorded probe data.</li></ul>
</body></html>"""


def _fmt(s: dict | None) -> str:
    if not s or not s.get("n"):
        return "—"
    p95 = f"{s['p95_ms']:.0f} ms" if s.get("p95_ms") is not None else "—"
    return f"{round(s['error_rate'] * 100):g}% / {p95}"


def _fmt_err(s: dict | None) -> str:
    if not s or not s.get("n"):
        return "—"
    return f"{round(s['error_rate'] * 100):g}%"


def render(data: dict[str, Any], out_dir: Path, map_png: Path | None = None) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    env = Environment(
        autoescape=select_autoescape(["html"]), trim_blocks=False, lstrip_blocks=False
    )
    ctx = {"d": _Obj(data), "fmt": _fmt, "fmt_err": _fmt_err}
    md_env = Environment(autoescape=False)
    md = md_env.from_string(MD).render(**ctx)
    png = base64.b64encode(map_png.read_bytes()).decode() if map_png and map_png.exists() else ""
    html = env.from_string(HTML).render(map_png=png, **ctx)
    md_path, html_path = out_dir / "report.md", out_dir / "report.html"
    md_path.write_text(md)
    html_path.write_text(html)
    return md_path, html_path


class _Obj(dict):
    """Attribute-style access over nested dicts/lists for the templates."""

    def __getattr__(self, k: str) -> Any:
        try:
            return _wrap(self[k])
        except KeyError:
            raise AttributeError(k) from None


def _wrap(v: Any) -> Any:
    if isinstance(v, dict):
        return _Obj(v)
    if isinstance(v, list):
        return [_wrap(x) for x in v]
    if isinstance(v, Finding):
        return _Obj(v.model_dump())
    return v
