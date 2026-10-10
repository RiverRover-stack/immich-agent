"""The closed loop: plan -> validate -> (approve) -> experiment -> analyze -> re-plan.

The planner is pluggable (formula today, LLM later). Whatever it returns is untrusted: every
proposal goes through the validator, and the executor re-checks the allowlist. Results feed the next
plan, so the order adapts to what was learned.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import docker
import httpx

from shutterstorm.analyzer.metrics import analyze_experiment
from shutterstorm.chaos.executor import FaultExecutor
from shutterstorm.chaos.faults import FaultError
from shutterstorm.chaos.journal import KillSwitch
from shutterstorm.chaos.policy import IMMICH_POLICY
from shutterstorm.config import Settings
from shutterstorm.experiment import run_experiment
from shutterstorm.planner.validator import Rejected, Validated, validate
from shutterstorm.schemas import Proposal

Notify = Callable[[str, str], None]
STEADY_TIMEOUT_S = 180.0


class Planner(Protocol):
    name: str

    def plan(
        self,
        topology: dict[str, Any],
        results: list[dict[str, Any]],
        baseline: dict[str, Any] | None = None,
        allowed_faults: tuple[str, ...] | None = None,
    ) -> list[Proposal]: ...


@dataclass
class SessionConfig:
    settings: Settings
    topology: dict[str, Any]
    planner: Planner
    out_dir: Path
    baseline: dict[str, Any] | None = None
    max_experiments: int = 4
    cooldown_s: float = 60.0
    warmup_s: float = 10.0
    approve: Callable[[Validated], bool] | None = None  # assisted mode: human gate
    allowed_faults: tuple[str, ...] | None = None  # restrict the session to certain faults
    state_dir: Path | None = None


@dataclass
class SessionResult:
    results: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    stopped_because: str = "completed"


def wait_until_steady(
    client: docker.DockerClient,
    settings: Settings,
    cooldown_s: float,
    notify: Notify,
    timeout_s: float = STEADY_TIMEOUT_S,
) -> bool:
    """Containers healthy and queues idle, then a fixed cooldown (CLAUDE.md section 5)."""
    deadline = time.time() + timeout_s
    headers = {"x-api-key": settings.api_key}
    while time.time() < deadline:
        healthy = True
        for name in IMMICH_POLICY.allowed:
            try:
                c = client.containers.get(name)
                c.reload()
                health = (c.attrs["State"].get("Health") or {}).get("Status")
                if c.status != "running" or health not in (None, "healthy"):
                    healthy = False
            except docker.errors.DockerException:
                healthy = False
        idle = False
        if healthy:
            try:
                r = httpx.get(f"{settings.immich_url}/api/jobs", headers=headers, timeout=5)
                jobs = r.json()
                idle = all(
                    q["jobCounts"]["active"] == 0 and q["jobCounts"]["waiting"] == 0
                    for q in jobs.values()
                )
            except (httpx.HTTPError, KeyError, ValueError):
                idle = False
        if healthy and idle:
            notify("steady", f"system steady; cooling down {cooldown_s:g}s")
            time.sleep(cooldown_s)
            return True
        time.sleep(2)
    notify("steady", "system did NOT return to a steady state before the timeout")
    return False


def run_session(
    cfg: SessionConfig,
    notify: Notify | None = None,
    client: docker.DockerClient | None = None,
) -> SessionResult:
    say: Notify = notify or (lambda kind, msg: None)
    client = client or docker.from_env()
    out = SessionResult()
    kill = KillSwitch(cfg.state_dir)
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    seen: set[tuple[str, str]] = set()

    for n in range(1, cfg.max_experiments + 1):
        if kill.is_set():
            out.stopped_because = "kill switch tripped"
            break

        validated = _next_valid(cfg, out, seen, say, n)
        if validated is None:
            out.stopped_because = "no remaining experiments"
            break
        spec = validated.spec
        spec.warmup_s = cfg.warmup_s
        say("plan", f"{spec.id}: {spec.fault} -> {spec.target} (planner: {spec.planner})")
        for note in validated.notes:
            say("note", note)
        if cfg.approve is not None and not cfg.approve(validated):
            out.skipped.append({"id": spec.id, "reason": "declined by operator"})
            seen.add((spec.target, spec.fault))
            continue

        run_dir = cfg.out_dir / f"{spec.id}-{spec.fault}-{spec.target.replace('immich_', '')}"
        seen.add((spec.target, spec.fault))
        try:
            events = run_experiment(
                spec, cfg.settings, run_dir, client=client, notify=say, state_dir=cfg.state_dir
            )
        except FaultError as e:
            out.skipped.append({"id": spec.id, "reason": f"refused: {e}"})
            say("error", f"{spec.id} refused: {e}")
            out.stopped_because = f"refused: {e}"
            break

        result = analyze_experiment(run_dir, cfg.baseline)
        result["run_dir"] = str(run_dir)
        result["planner_reason"] = spec.reason
        out.results.append(result)
        (cfg.out_dir / "results.json").write_text(json.dumps(out.results, indent=2))
        say(
            "result",
            f"{spec.id}: {result['outcome']} · score {result['score']['total']} · "
            f"recovery {result['recovery_s']}s · hit: {result['journeys_hit_as_expected']}",
        )

        if not events["fault"]["undone_verified"]:
            # The one condition under which we stop everything: the system may be left broken.
            FaultExecutor(client, IMMICH_POLICY, state_dir=cfg.state_dir).panic(on_event=say)
            out.stopped_because = "fault could not be verified undone"
            break
        if n < cfg.max_experiments:
            wait_until_steady(client, cfg.settings, cfg.cooldown_s, say)
    else:
        out.stopped_because = "experiment budget reached"

    (cfg.out_dir / "results.json").write_text(json.dumps(out.results, indent=2))
    return out


def _next_valid(
    cfg: SessionConfig,
    out: SessionResult,
    seen: set[tuple[str, str]],
    say: Notify,
    n: int,
) -> Validated | None:
    proposals = cfg.planner.plan(
        cfg.topology, out.results, cfg.baseline, allowed_faults=cfg.allowed_faults
    )
    for p in proposals:
        if cfg.allowed_faults and p.fault not in cfg.allowed_faults:
            continue
        if (p.target, p.fault) in seen:
            continue
        try:
            return validate(p, cfg.topology, f"E-{n:02d}", baseline=cfg.baseline)
        except Rejected as e:
            say("reject", f"proposal {p.target}/{p.fault} rejected: {e}")
            out.skipped.append({"id": f"E-{n:02d}", "reason": f"rejected: {e}"})
    return None
