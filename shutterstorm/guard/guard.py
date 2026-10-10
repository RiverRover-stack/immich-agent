"""The guard. Fixed thresholds, no AI, no judgement: it must behave identically every time.

`Guard.check()` is plugged into FaultExecutor as `should_abort`. It returns a reason string when
the experiment must stop (the executor then undoes the fault and verifies the undo), else None.

Trip conditions (CLAUDE.md section 4.5):
  1. a PROTECTED journey's error rate over the trailing window exceeds `error_rate_pct`
  2. a protected journey's p95 exceeds max(p95_multiple x baseline, latency_floor_ms)
  3. a container other than the fault target is no longer running (blast radius)
(The hard maximum duration is enforced by the executor's TTL.)
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from shutterstorm.workload.stats import RollingStats

# Journeys whose health the guard protects by default: the ones no single-service fault in our
# catalog should break. Experiments may override per fault.
DEFAULT_PROTECTED = ("ping", "auth_read", "browse")


@dataclass(frozen=True)
class GuardConfig:
    protected: tuple[str, ...] = DEFAULT_PROTECTED
    error_rate_pct: float = 10.0
    window_s: float = 10.0  # "for 10 s": error rate is judged over this trailing window
    p95_multiple: float = 3.0
    latency_floor_ms: float = 150.0  # never trip on latency below this (baseline noise)
    min_samples: int = 3  # need this many samples in the window before judging
    baseline_p95_ms: dict[str, float] = field(default_factory=dict)
    container_check_every_s: float = 1.0


class Guard:
    def __init__(
        self,
        stats: RollingStats,
        config: GuardConfig | None = None,
        containers: Callable[[], dict[str, str]] | None = None,
        watched: frozenset[str] = frozenset(),
        exclude: frozenset[str] = frozenset(),
        clock: Callable[[], float] = time.time,
    ) -> None:
        """`containers()` returns {name: status}. `exclude` is the fault target (expected to change)."""
        self.stats = stats
        self.config = config or GuardConfig()
        self._containers = containers
        self._watched = watched
        self._exclude = exclude
        self._clock = clock
        self._last_container_check = 0.0
        self.tripped: str | None = None
        self.checks = 0

    def check(self) -> str | None:
        """Return a reason to abort, or None. Latches: once tripped it keeps returning the reason."""
        if self.tripped:
            return self.tripped
        self.checks += 1
        reason = self._check_journeys() or self._check_containers()
        if reason:
            self.tripped = reason
        return reason

    def _check_journeys(self) -> str | None:
        c = self.config
        now = self._clock()
        for journey in c.protected:
            s = self.stats.summary(journey, now=now, window_s=c.window_s)
            if s["count"] < c.min_samples:
                continue
            if s["err_pct"] > c.error_rate_pct:
                return (
                    f"protected journey '{journey}' error rate {s['err_pct']:.0f}% "
                    f"> {c.error_rate_pct:g}% over {c.window_s:g}s"
                )
            base = c.baseline_p95_ms.get(journey)
            if base is not None and s["p95_ms"] is not None:
                limit = max(c.p95_multiple * base, c.latency_floor_ms)
                if s["p95_ms"] > limit:
                    return (
                        f"protected journey '{journey}' p95 {s['p95_ms']:.0f}ms "
                        f"> {limit:.0f}ms ({c.p95_multiple:g}x baseline {base:.0f}ms)"
                    )
        return None

    def _check_containers(self) -> str | None:
        if self._containers is None:
            return None
        now = self._clock()
        if now - self._last_container_check < self.config.container_check_every_s:
            return None
        self._last_container_check = now
        status = self._containers()
        for name in sorted(self._watched - self._exclude):
            if status.get(name) != "running":
                return f"unexpected container state: {name} is {status.get(name, 'missing')!r}"
        return None


def docker_container_states(client: Any, names: frozenset[str]) -> Callable[[], dict[str, str]]:
    """Adapter: current {container: status} from the Docker SDK."""

    def read() -> dict[str, str]:
        out: dict[str, str] = {}
        for n in names:
            try:
                c = client.containers.get(n)
                c.reload()
                out[n] = c.status
            except Exception:  # noqa: BLE001 - any failure to read means "not running"
                out[n] = "missing"
        return out

    return read
