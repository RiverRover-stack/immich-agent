"""The final evaluation flow: baseline -> inject ML fault -> observe -> restore -> verify -> compare.

One command that exercises every stage of the target flow and checks each one explicitly:

    Start -> Baseline sampling -> Store baseline -> Inject fault (record start) -> Continue probes
          -> Restore (recovery action or safety stop) -> Verify recovery -> Compare before/during/after
          -> Save evidence and report
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import docker

from shutterstorm.analyzer.metrics import analyze_experiment, compute_baseline
from shutterstorm.config import Settings
from shutterstorm.discover.topology import build_topology
from shutterstorm.experiment import run_experiment
from shutterstorm.llm.client import LLMClient
from shutterstorm.planner.validator import validate
from shutterstorm.report.render import build_report_data, render
from shutterstorm.schemas import Prediction, Proposal
from shutterstorm.workload.probes import ProbeRunner

Notify = Callable[[str, str], None]


@dataclass
class Stage:
    name: str
    ok: bool
    detail: str


@dataclass
class EvaluationResult:
    out_dir: Path
    stages: list[Stage] = field(default_factory=list)
    result: dict[str, Any] | None = None
    report_md: Path | None = None
    report_html: Path | None = None

    @property
    def passed(self) -> bool:
        return bool(self.stages) and all(s.ok for s in self.stages)


def sample_baseline(settings: Settings, out_dir: Path, seconds: float) -> dict[str, Any]:
    """Run the probes with no fault and store the steady-state reference."""
    runner = ProbeRunner(settings, out_dir=out_dir / "baseline-run")
    asyncio.run(runner.run(seconds))
    baseline = compute_baseline(out_dir / "baseline-run", trim_s=3.0)
    (out_dir / "baseline.json").write_text(json.dumps(baseline, indent=2))
    return baseline


def run_evaluation(
    settings: Settings,
    out_dir: Path,
    baseline_s: float = 60.0,
    target: str = "ml",
    fault: str = "stop",
    ttl_s: float = 30.0,
    llm: LLMClient | None = None,
    notify: Notify | None = None,
    client: docker.DockerClient | None = None,
) -> EvaluationResult:
    say: Notify = notify or (lambda kind, msg: None)
    out = EvaluationResult(out_dir=out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    client = client or docker.from_env()

    def stage(name: str, ok: bool, detail: str) -> bool:
        out.stages.append(Stage(name, ok, detail))
        say("stage", f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
        return ok

    # 1. Baseline sampling + store baseline
    say("stage", f"baseline sampling for {baseline_s:g}s (application + ML probes)")
    baseline = sample_baseline(settings, out_dir, baseline_s)
    clean = all((s["error_rate"] or 0) == 0 for s in baseline["journeys"].values())
    stage(
        "Baseline sampled and stored",
        clean and baseline["queues"]["idle"],
        f"{sum(s['n'] for s in baseline['journeys'].values())} requests, "
        f"errors {'none' if clean else 'PRESENT'}, ML completion median "
        f"{baseline['uploads']['median_indexed_s']}s, queues idle {baseline['queues']['idle']}",
    )
    if not clean:
        say("error", "baseline is not clean: refusing to inject a fault into an unhealthy system")
        _write_summary(out)
        return out
    time.sleep(3)

    # 2. Inject the ML fault through the same gate the agent uses
    topology = build_topology(docker_client=client)
    proposal = Proposal(
        target=target,
        fault=fault,
        ttl_s=ttl_s,
        hypothesis=(
            "With ML down, search and upload processing fail while ping, auth and browse stay "
            "healthy; uploads are still accepted."
        ),
        predictions=[
            Prediction(journey="ping", expected="healthy", confidence=0.9),
            Prediction(journey="auth_read", expected="healthy", confidence=0.9),
            Prediction(journey="browse", expected="healthy", confidence=0.9),
            Prediction(journey="search", expected="fail", confidence=0.9),
            Prediction(journey="upload", expected="healthy", confidence=0.7),
            Prediction(journey="upload_processing", expected="fail", confidence=0.8),
        ],
        reason="the ML service is a dependency the compose file does not declare",
        planner="manual",
    )
    v = validate(proposal, topology, "EVAL-01", baseline=baseline)
    run_dir = out_dir / "experiment"
    events = run_experiment(v.spec, settings, run_dir, client=client, notify=say)
    f = events.get("fault") or {}

    stage(
        "Fault injected, exact start recorded",
        bool(f.get("applied_at")),
        f"{f.get('fault')} on {f.get('target')} at {time.strftime('%H:%M:%S', time.localtime(f.get('applied_at', 0)))}",
    )
    stage(
        "Safety: restored by TTL, guard or stop",
        bool(f.get("undone_verified")),
        f"outcome {f.get('outcome')} ({f.get('reason')}), undo verified {f.get('undone_verified')}",
    )
    if not f.get("undone_verified"):
        _write_summary(out)
        return out

    # 3. Compare before / during / after
    result = analyze_experiment(run_dir, baseline)
    result["run_dir"] = str(run_dir)
    result["planner_reason"] = v.spec.reason
    out.result = result
    (out_dir / "result.json").write_text(json.dumps(result, indent=2))

    hit = result["journeys_hit_as_expected"]
    stage(
        "Probes showed the measured impact",
        bool(hit),
        f"journeys affected: {', '.join(hit) or 'none'}; collateral: "
        f"{', '.join(result['journeys_hit_unexpectedly']) or 'none'}",
    )
    stage(
        "Recovery verified (consecutive clean checks)",
        result["recovered"],
        f"clean for 10 consecutive seconds {result['recovery_s']}s after undo"
        if result["recovered"]
        else "did not recover within the observation window",
    )
    stage(
        "Before / during / after compared",
        result["after"]["measured"],
        "all journeys back to baseline"
        if result["after"]["all_back_to_baseline"]
        else f"not back to baseline: {result['after']['not_back']}",
    )

    # 4. Evidence + report
    data = build_report_data([result], topology, baseline, client=llm, planner_history=[])
    map_png = Path("reports/dependency-map.png")
    out.report_md, out.report_html = render(data, out_dir, map_png if map_png.exists() else None)
    stage(
        "Evidence and report saved",
        out.report_md.exists() and out.report_html.exists(),
        f"{out.report_md.name}, {out.report_html.name}; findings by {data['provenance']['writer']}",
    )
    _write_summary(out)
    return out


def _write_summary(out: EvaluationResult) -> None:
    (out.out_dir / "stages.json").write_text(
        json.dumps({"passed": out.passed, "stages": [s.__dict__ for s in out.stages]}, indent=2)
    )
