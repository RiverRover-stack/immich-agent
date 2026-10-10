"""Ranked findings: deterministic templates, optionally rewritten by the small model.

Trust model: the model only ever sees structured evidence. Every number it writes is checked
against the evidence; an unverifiable finding is replaced by its deterministic template, so a
report can never contain a figure that did not come from a measurement.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from shutterstorm.llm.client import LLMClient, LLMError

Severity = Literal["critical", "high", "medium", "low"]
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
# Small counts are common prose ("all 3 uploads"); anything else must trace to the evidence.
ALWAYS_ALLOWED = {0.0, 1.0, 2.0, 3.0, 100.0}


class Finding(BaseModel):
    id: str = ""
    title: str
    severity: Severity
    evidence: list[str] = Field(
        min_length=1, description="experiment ids (E-01) or audit ids (A-01)"
    )
    cause: str
    impact: str
    fix: str
    source: Literal["experiment", "static", "mixed"] = "experiment"
    written_by: str = "template"  # template | llm


class FindingsDoc(BaseModel):
    findings: list[Finding]


# ------------------------------------------------------------ number checker
_ID_RE = re.compile(r"\b[A-Za-z]{1,3}-\d+\b|\bv?\d+\.\d+\.\d+\b")
_NUM_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")


def numbers_in_text(text: str) -> list[float]:
    stripped = _ID_RE.sub(" ", text)
    return [float(m) for m in _NUM_RE.findall(stripped)]


def allowed_numbers(obj: Any) -> set[float]:
    """Every figure present in the evidence, plus percent and rounded forms of each."""
    out: set[float] = set(ALWAYS_ALLOWED)

    def add(x: float) -> None:
        out.add(round(x, 6))
        out.add(round(x, 1))
        out.add(round(x))
        out.add(float(int(x)))
        if 0 <= x <= 1:  # a rate: 0.333 -> 33.3% / 33%
            out.add(round(x * 100, 1))
            out.add(round(x * 100))

    def walk(o: Any) -> None:
        if isinstance(o, bool) or o is None:
            return
        if isinstance(o, (int, float)):
            add(float(o))
        elif isinstance(o, str):
            for n in numbers_in_text(o):
                add(n)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)

    walk(obj)
    return out


def unverified_numbers(text: str, allowed: set[float]) -> list[float]:
    bad = []
    for n in numbers_in_text(text):
        if not any(abs(n - a) <= max(0.051, abs(a) * 0.01) for a in allowed):
            bad.append(n)
    return bad


# Words that assert something our measurements cannot show. A figure checker cannot catch these.
NEVER_SUPPORTED = (
    "data loss",
    "lost data",
    "data is lost",
    "corrupt",
    "permanently lost",
    "security",
    "breach",
    "unpredictably",
)
NEEDS_COLLATERAL = ("cascad", "ripple", "domino")  # only if a journey broke unexpectedly


def overclaims(f: Finding, evidence: dict[str, Any]) -> list[str]:
    text = f"{f.title} {f.cause} {f.impact} {f.fix}".lower()
    found = [w for w in NEVER_SUPPORTED if w in text]
    collateral = any(e["hit_unexpectedly"] for e in evidence["experiments"])
    if not collateral:
        found += [w for w in NEEDS_COLLATERAL if w in text]
    return found


def check_finding(f: Finding, evidence: dict[str, Any], allowed: set[float]) -> list[str]:
    problems = []
    claims = overclaims(f, evidence)
    if claims:
        problems.append(f"claims not supported by the evidence: {claims}")
    known_ids = set(evidence["ids"])
    for e in f.evidence:
        if e not in known_ids:
            problems.append(f"unknown evidence id {e!r}")
    for field_name in ("title", "cause", "impact", "fix"):
        bad = unverified_numbers(getattr(f, field_name), allowed)
        if bad:
            problems.append(f"{field_name} cites unverified numbers {bad}")
    return problems


# --------------------------------------------------------- evidence bundle
def build_evidence(results: list[dict[str, Any]], topology: dict[str, Any]) -> dict[str, Any]:
    """The only thing the writer is allowed to see. Compact, structured, citable by id."""
    exps = []
    for r in results:
        exps.append(
            {
                "id": r["experiment_id"],
                "target": r["target"],
                "fault": r["fault"],
                "planner": r.get("planner"),
                "outcome": r["outcome"],
                "fault_duration_s": r["fault_duration_s"],
                "undone_verified": r["undone_verified"],
                "journeys": {
                    j: {
                        "observed": v["observed"],
                        "error_rate_pre": (v.get("pre") or {}).get("error_rate"),
                        "error_rate_during": (v.get("during") or {}).get("error_rate"),
                        "requests_during": (v.get("during") or {}).get("n"),
                        "p95_ms_pre": (v.get("pre") or {}).get("p95_ms"),
                        "p95_ms_during": (v.get("during") or {}).get("p95_ms"),
                        "error_rate_after": (v.get("after") or {}).get("error_rate"),
                    }
                    for j, v in r["journeys"].items()
                },
                "hit_as_expected": r["journeys_hit_as_expected"],
                "hit_unexpectedly": r["journeys_hit_unexpectedly"],
                "recovery_s": r["recovery_s"],
                "recovered": r["recovered"],
                "back_to_baseline": r["after"]["all_back_to_baseline"],
                "upload_attempts_during_fault": r["score"].get("upload_attempts_during_fault"),
                "uploads_accepted_during_fault": r["async"]["accepted_during_fault"],
                "uploads_processed_during_fault": r["async"]["processed_during_fault"],
                "uploads_never_indexed": r["async"]["never_indexed_during_fault"],
                "queue_failed_peak": max(
                    (q["failed"] for q in r["queue_peak"].values()), default=0
                ),
                "prediction_accuracy": r["predictions"]["accuracy"],
                "score": r["score"]["total"],
            }
        )
    audit = [
        {"id": a["id"], "severity": a["severity"], "title": a["title"], "services": a["services"]}
        for a in topology.get("audit", [])
    ]
    ids = [e["id"] for e in exps] + [a["id"] for a in audit]
    return {"experiments": exps, "static_audit": audit, "ids": ids}


# ------------------------------------------------------------- templates
def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{round(x * 100):g}%"


def template_findings(evidence: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []
    for e in evidence["experiments"]:
        tgt = e["target"].replace("immich_", "")
        hit = e["hit_as_expected"]

        if e["uploads_accepted_during_fault"] and e["uploads_never_indexed"]:
            out.append(
                Finding(
                    title=f"Uploads accepted during a {tgt} outage are never fully processed",
                    severity="high",
                    evidence=[e["id"]],
                    cause=(
                        f"Background jobs that need {tgt} fail while it is down and are not retried "
                        f"after it returns. During {e['id']}, {e['uploads_accepted_during_fault']} "
                        f"uploads were accepted and {e['uploads_never_indexed']} were never indexed; "
                        f"the queues' failed counter peaked at {e['queue_failed_peak']}."
                    ),
                    impact=(
                        "Users see a successful upload, but the photo is missing from content "
                        "search, and queue-based monitoring shows nothing wrong."
                    ),
                    fix=(
                        "After a dependency recovers, automatically run the missing-jobs command "
                        "(a manual check showed it indexes the stuck assets); alert on job-handler "
                        "errors rather than queue counters; add retries with backoff."
                    ),
                )
            )

        if not e["recovered"]:
            out.append(
                Finding(
                    title=f"The system did not return to normal after the {tgt} fault was undone",
                    severity="high",
                    evidence=[e["id"]],
                    cause=f"After {e['id']} the journeys were not clean for 10 consecutive seconds.",
                    impact="A transient fault becomes a persistent outage until someone intervenes.",
                    fix="Add a health-driven recovery action and alert when recovery exceeds a limit.",
                )
            )

        if e["hit_unexpectedly"]:
            js = ", ".join(e["hit_unexpectedly"])
            sizes = ", ".join(
                f"{j}: {e['journeys'][j]['requests_during']} request(s)"
                for j in e["hit_unexpectedly"]
            )
            smallest = min(e["journeys"][j]["requests_during"] or 0 for j in e["hit_unexpectedly"])
            out.append(
                Finding(
                    title=f"Failing {tgt} also broke {js}, which the map says do not depend on it",
                    severity="high" if smallest >= 5 else "medium",
                    evidence=[e["id"]],
                    cause=(
                        f"In {e['id']} the journeys {js} degraded although they are not mapped to "
                        f"{tgt} ({sizes})."
                        + (
                            " This rests on very few requests and should be re-run to confirm."
                            if smallest < 5
                            else ""
                        )
                    ),
                    impact="The dependency map is incomplete: there is a hidden coupling.",
                    fix="Correct journeys.yaml, then decouple the path or add a fallback.",
                )
            )

        hard = [j for j in hit if (e["journeys"][j]["error_rate_during"] or 0) >= 0.5]
        if hard and e["fault"] in ("stop", "pause"):
            core = {"ping", "auth_read", "browse"} & set(hard)
            rates = ", ".join(f"{j} {_pct(e['journeys'][j]['error_rate_during'])}" for j in hard)
            verb = {"stop": "stopped", "pause": "paused"}[e["fault"]]
            out.append(
                Finding(
                    title=f"No degradation path: {', '.join(hard)} fail outright when {tgt} is {verb}",
                    severity="high" if (core or len(hard) >= 3) else "medium",
                    evidence=[e["id"]],
                    cause=f"Error rates during {e['id']}: {rates}. There is no cached or reduced mode.",
                    impact=f"Every feature that needs {tgt} is fully unavailable for the whole outage.",
                    fix="Add graceful degradation (cache, queue-and-retry, clear user messaging).",
                )
            )

        if e["fault"] == "cpu_throttle":
            slow = []
            for j, v in e["journeys"].items():
                a, b = v["p95_ms_pre"], v["p95_ms_during"]
                if a and b and b > 3 * a and b > 150:
                    slow.append(f"{j} p95 {a} ms to {b} ms")
            if slow:
                out.append(
                    Finding(
                        title=f"CPU starvation of {tgt} sharply raises latency",
                        severity="medium",
                        evidence=[e["id"]],
                        cause="Observed in " + e["id"] + ": " + "; ".join(slow) + ".",
                        impact="Users experience a slow application before anything fails outright.",
                        fix="Set CPU/memory limits and reservations so heavy jobs cannot starve the API.",
                    )
                )

    fixes = {
        "Hidden dependency": (
            "high",
            "Declare the dependency (depends_on with a health condition) and alert on its health.",
        ),
        "Single instance of stateful": (
            "high",
            "Add replicas or failover; for the database use a replicated or managed setup.",
        ),
        "No CPU or memory limits": ("medium", "Set per-service CPU and memory limits."),
        "Restart policy reacts to exit only": (
            "medium",
            "Add a watchdog or orchestrator liveness probes so hung processes are restarted.",
        ),
        "Single instance of stateless": ("medium", "Run more than one instance behind a balancer."),
        "No healthcheck": ("low", "Define healthchecks so failures are detected without an exit."),
    }
    for a in evidence["static_audit"]:
        sev, fix = next(
            ((s, f) for k, (s, f) in fixes.items() if a["title"].startswith(k)),
            (a["severity"], "Review and remediate."),
        )
        out.append(
            Finding(
                title=a["title"],
                severity=sev,  # type: ignore[arg-type]
                evidence=[a["id"]],
                cause="Static configuration (no experiment): " + ", ".join(a["services"]) + ".",
                impact="Raises the likelihood and blast radius of the failures tested above.",
                fix=fix,
                source="static",
            )
        )

    return rank(out)


def rank(findings: list[Finding]) -> list[Finding]:
    ordered = sorted(
        findings, key=lambda f: (SEVERITY_ORDER[f.severity], f.source == "static", f.title)
    )
    for i, f in enumerate(ordered, 1):
        f.id = f"F-{i:02d}"
    return ordered


# ------------------------------------------------------------- LLM writer
WRITER_SYSTEM = """You write the findings of a chaos-engineering report for engineers.
You receive STRUCTURED EVIDENCE measured by code, plus DRAFT findings written from templates.
Rewrite the drafts into clear, specific findings. Rules:
- Use ONLY numbers that appear in the evidence. Do not compute, estimate or round new figures.
- Every finding must cite evidence ids (E-xx experiments, A-xx audit items) from the evidence.
- Do not invent components, causes or behaviours that the evidence does not show. If something is
  uncertain, say so in the text.
- Do not overstate. An upload that is accepted but not search-indexed is NOT "data loss" (the file is
  stored). Do not say failures "cascade" or are "unpredictable" unless hit_unexpectedly is non-empty.
  Describe exactly what was measured.
- Keep every draft finding (same evidence ids); you may improve wording and tighten the fix.
- severity must be one of critical, high, medium, low. Do not raise severity above the draft's.
Return JSON: {"findings": [{title, severity, evidence, cause, impact, fix, source}]}."""


def write_findings(
    evidence: dict[str, Any], client: LLMClient | None
) -> tuple[list[Finding], dict[str, Any]]:
    """Returns (findings, provenance). Never raises: worst case is the template findings."""
    drafts = template_findings(evidence)
    prov: dict[str, Any] = {"writer": "template", "model": None, "rejected": [], "note": ""}
    if client is None:
        prov["note"] = "no LLM configured; deterministic templates used"
        return drafts, prov

    allowed = allowed_numbers(evidence)
    user = (
        "EVIDENCE (json):\n"
        + json.dumps(evidence, indent=1)
        + "\n\nDRAFT FINDINGS (json):\n"
        + json.dumps([f.model_dump() for f in drafts], indent=1)
    )
    feedback = ""
    for attempt in (1, 2):
        try:
            doc = client.complete_json(
                "small",
                WRITER_SYSTEM,
                user + feedback,
                FindingsDoc,
                max_tokens=3500,
                temperature=0.1,
            )
        except LLMError as e:
            prov["note"] = f"LLM unavailable ({e}); deterministic templates used"
            return drafts, prov

        # Severity ceiling = the most severe draft that shares the same evidence.
        ceiling: dict[tuple[str, ...], int] = {}
        wanted: dict[tuple[str, ...], int] = {}
        for d in drafts:
            key = tuple(sorted(d.evidence))
            ceiling[key] = min(ceiling.get(key, 99), SEVERITY_ORDER[d.severity])
            wanted[key] = wanted.get(key, 0) + 1

        problems: list[str] = []
        got: dict[tuple[str, ...], int] = {}
        for f in doc.findings:
            p = check_finding(f, evidence, allowed)
            key = tuple(sorted(f.evidence))
            got[key] = got.get(key, 0) + 1
            if key in ceiling and SEVERITY_ORDER[f.severity] < ceiling[key]:
                p.append("severity raised above the draft")
            problems += [f"{f.title[:40]!r}: {x}" for x in p]
        for key, n in wanted.items():
            if got.get(key, 0) < n:
                problems.append(f"findings for evidence {list(key)} were dropped")

        if not problems:
            for f in doc.findings:
                f.written_by = "llm"
            prov.update(writer="llm", model=client.model_for("small"), note="all figures verified")
            return rank(doc.findings), prov
        prov["rejected"].append({"attempt": attempt, "problems": problems})
        feedback = (
            "\n\nYour previous answer was rejected: " + "; ".join(problems) + ". Fix and retry."
        )

    prov["note"] = "LLM output failed verification twice; deterministic templates used"
    return drafts, prov
