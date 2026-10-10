"""One experiment end to end: warm up -> arm guard -> inject -> undo -> watch recovery -> record.

Probes run on a background asyncio loop; the executor (blocking, with the guard as its abort check)
runs on the calling thread. Everything lands in one run folder:

    probes.csv  queues.csv  uploads.csv   raw evidence, one row per request / sample
    events.json                           fault timeline + guard verdict (the analyzer's anchor)
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import docker

from shutterstorm.chaos.executor import FaultExecutor, FaultRun
from shutterstorm.chaos.policy import IMMICH_POLICY
from shutterstorm.config import Settings
from shutterstorm.guard.guard import DEFAULT_PROTECTED, Guard, GuardConfig, docker_container_states
from shutterstorm.workload.probes import ProbeRunner

WARMUP_S = 10.0
RECOVERY_STABLE_S = 10.0  # "within tolerance for 10 consecutive seconds" (CLAUDE.md)
RECOVERY_TIMEOUT_S = 120.0
COOLDOWN_S = 15.0  # also the 'after' observation window once recovery is confirmed

Notify = Callable[[str, str], None]


@dataclass
class ExperimentSpec:
    id: str
    target: str
    fault: str
    params: dict[str, Any] = field(default_factory=dict)
    ttl_s: float = 30.0
    protected: tuple[str, ...] = DEFAULT_PROTECTED
    hypothesis: str = ""
    reason: str = ""
    predictions: list[dict[str, Any]] = field(default_factory=list)
    planner: str = "manual"  # who chose this: manual | fallback | llm | paste
    guard: GuardConfig | None = None  # override thresholds (the drill lowers them on purpose)
    warmup_s: float = WARMUP_S
    recovery_timeout_s: float = RECOVERY_TIMEOUT_S
    cooldown_s: float = COOLDOWN_S
    upload: bool = True


class _BackgroundProbes:
    """Run a ProbeRunner on its own thread + event loop so the main thread can block on faults."""

    def __init__(self, runner: ProbeRunner) -> None:
        self.runner = runner
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._main, name="probes", daemon=True)

    def _main(self) -> None:
        try:
            asyncio.run(self.runner.run(None))
        except BaseException as e:  # noqa: BLE001 - surfaced to the caller via .error
            self.error = e

    def start(self, wait_s: float = 15.0) -> None:
        self._thread.start()
        deadline = time.time() + wait_s
        while self.runner.started_at is None and time.time() < deadline:
            if self.error:
                raise RuntimeError(f"probes failed to start: {self.error}") from self.error
            time.sleep(0.1)
        if self.runner.started_at is None:
            raise RuntimeError("probes did not start (is Immich up? is the API key valid?)")

    def stop(self) -> None:
        self.runner.stop()
        self._thread.join(timeout=20)


def _wait_for_recovery(runner: ProbeRunner, journeys: list[str], timeout_s: float) -> float | None:
    """Seconds until every given journey has been clean for RECOVERY_STABLE_S, or None on timeout."""
    start = time.time()
    while time.time() - start < timeout_s:
        clean = True
        for j in journeys:
            s = runner.stats.summary(j, window_s=RECOVERY_STABLE_S)
            if s["count"] < 2 or s["errors"] > 0:
                clean = False
                break
        if clean:
            return time.time() - start
        time.sleep(0.5)
    return None


def run_experiment(
    spec: ExperimentSpec,
    settings: Settings,
    out_dir: Path,
    client: docker.DockerClient | None = None,
    notify: Notify | None = None,
    runner_factory: Callable[..., ProbeRunner] = ProbeRunner,
    state_dir: Path | None = None,
) -> dict[str, Any]:
    """Run one experiment and return the events record (also written to events.json)."""
    say: Notify = notify or (lambda kind, msg: None)
    client = client or docker.from_env()
    executor = FaultExecutor(client, IMMICH_POLICY, state_dir=state_dir)
    target = IMMICH_POLICY.resolve(spec.target)

    runner = runner_factory(settings, out_dir=out_dir)
    if not spec.upload:
        runner.sample_dir = None
    bg = _BackgroundProbes(runner)
    say("probes", "starting probes")
    bg.start()
    events: dict[str, Any] = {
        "spec": _spec_to_json(spec),
        "target": target,
        "started_at": time.time(),
    }
    try:
        say("warmup", f"collecting {spec.warmup_s:g}s of healthy data")
        time.sleep(spec.warmup_s)

        guard = Guard(
            runner.stats,
            spec.guard or GuardConfig(protected=spec.protected),
            containers=docker_container_states(client, IMMICH_POLICY.allowed),
            watched=IMMICH_POLICY.allowed,
            exclude=frozenset({target}),
        )
        fault_run: FaultRun = executor.run(
            spec.target,
            spec.fault,
            spec.params,
            ttl_s=spec.ttl_s,
            should_abort=guard.check,
            on_event=say,
        )
        events["fault"] = asdict(fault_run)
        events["guard"] = {"tripped": guard.tripped, "checks": guard.checks}

        if fault_run.undone_verified:
            say("recovery", "watching for recovery")
            recoverable = [j for j in runner.journey_names if j != "upload"]
            rec = _wait_for_recovery(runner, recoverable, spec.recovery_timeout_s)
            events["recovery_wait_s"] = rec
            events["recovered"] = rec is not None
            say(
                "recovery",
                f"recovered after waiting {rec:.0f}s"
                if rec is not None
                else "NOT recovered before timeout",
            )
            time.sleep(spec.cooldown_s)
        else:
            events["recovered"] = False
            say("error", "fault was not verified undone: run `shutterstorm panic`")
    finally:
        events["ended_at"] = time.time()
        bg.stop()
        (out_dir / "events.json").write_text(json.dumps(events, indent=2, default=str))
    return events


def _spec_to_json(spec: ExperimentSpec) -> dict[str, Any]:
    d = asdict(spec)
    d["protected"] = list(spec.protected)
    return d


def drill_spec() -> ExperimentSpec:
    """A mild fault with a deliberately LOW threshold: proves the guard trips and rolls back.

    Stopping ML only breaks search. We protect 'search' with a tight window so the guard must
    fire within a few seconds, long before the 60 s TTL would have ended the fault.
    """
    return ExperimentSpec(
        id="DRILL",
        target="ml",
        fault="stop",
        ttl_s=60.0,
        protected=("search",),
        hypothesis="guard aborts and rolls back when a protected journey breaches its threshold",
        planner="manual",
        guard=GuardConfig(protected=("search",), error_rate_pct=10.0, window_s=6.0, min_samples=2),
        warmup_s=8.0,
        upload=False,
    )
