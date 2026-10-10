"""Probe result record and rolling statistics (shared by the live view and the guard)."""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class ProbeResult:
    ts: float
    journey: str
    ok: bool
    status: int  # HTTP status, or 0 for timeout / connection error
    latency_ms: float


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile; q in (0, 1]. None when there is no data."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


class RollingStats:
    """Per-journey results over a sliding time window. Thread-safe (probes write, guard reads)."""

    def __init__(self, window_s: float = 10.0) -> None:
        self.window_s = window_s
        self._by_journey: dict[str, deque[ProbeResult]] = {}
        self._lock = threading.Lock()

    def add(self, result: ProbeResult) -> None:
        with self._lock:
            self._by_journey.setdefault(result.journey, deque()).append(result)

    def summary(
        self, journey: str, now: float | None = None, window_s: float | None = None
    ) -> dict:
        """Stats for one journey. `window_s` narrows the view (it cannot exceed the stored window)."""
        now = time.time() if now is None else now
        with self._lock:
            dq = self._by_journey.get(journey)
            if dq is None:
                items: list[ProbeResult] = []
            else:
                while dq and dq[0].ts < now - self.window_s:
                    dq.popleft()
                cutoff = now - (window_s if window_s is not None else self.window_s)
                items = [r for r in dq if r.ts >= cutoff]
        count = len(items)
        errors = sum(1 for r in items if not r.ok)
        return {
            "count": count,
            "errors": errors,
            "err_pct": (100.0 * errors / count) if count else 0.0,
            "p95_ms": percentile([r.latency_ms for r in items if r.ok], 0.95),
            "last_status": items[-1].status if items else None,
        }
