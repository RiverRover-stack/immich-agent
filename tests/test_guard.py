from shutterstorm.guard.guard import Guard, GuardConfig
from shutterstorm.workload.stats import ProbeResult, RollingStats

NOW = 1000.0


def feed(stats, journey, n, ok=True, ms=10.0, start=NOW - 9, step=1.0):
    for i in range(n):
        stats.add(ProbeResult(start + i * step, journey, ok, 200 if ok else 0, ms))


def guard(stats, **kw):
    cfg = GuardConfig(**{k: v for k, v in kw.items() if k in GuardConfig.__dataclass_fields__})
    return Guard(
        stats,
        cfg,
        containers=kw.get("containers"),
        watched=kw.get("watched", frozenset()),
        exclude=kw.get("exclude", frozenset()),
        clock=lambda: NOW,
    )


def test_healthy_system_does_not_trip():
    s = RollingStats(window_s=20)
    feed(s, "ping", 10)
    feed(s, "browse", 10)
    assert guard(s).check() is None


def test_error_rate_over_threshold_trips_with_reason():
    s = RollingStats(window_s=20)
    feed(s, "browse", 8)
    feed(s, "browse", 2, ok=False, start=NOW - 1)  # 20% errors > 10%
    reason = guard(s).check()
    assert reason and "browse" in reason and "20%" in reason


def test_unprotected_journey_failing_is_expected_not_a_trip():
    s = RollingStats(window_s=20)
    feed(s, "search", 8, ok=False)  # ML is down on purpose
    feed(s, "ping", 8)
    assert guard(s).check() is None


def test_too_few_samples_never_trips():
    s = RollingStats(window_s=20)
    feed(s, "ping", 2, ok=False)
    assert guard(s).check() is None


def test_latency_relative_to_baseline_with_noise_floor():
    s = RollingStats(window_s=20)
    feed(s, "browse", 10, ms=100.0)  # 100ms vs baseline 10ms = 10x, but under the 150ms floor
    assert guard(s, baseline_p95_ms={"browse": 10.0}).check() is None

    s2 = RollingStats(window_s=20)
    feed(s2, "browse", 10, ms=400.0)  # 40x baseline AND over the floor
    reason = guard(s2, baseline_p95_ms={"browse": 10.0}).check()
    assert reason and "p95" in reason


def test_unexpected_container_death_trips_but_target_is_excluded():
    s = RollingStats(window_s=20)
    states = {
        "immich_server": "running",
        "immich_postgres": "exited",
        "immich_machine_learning": "exited",
    }
    watched = frozenset(states)
    g = guard(
        s,
        containers=lambda: states,
        watched=watched,
        exclude=frozenset({"immich_machine_learning"}),
    )
    reason = g.check()
    assert reason and "immich_postgres" in reason  # the target (ML) alone would not have tripped

    states["immich_postgres"] = "running"
    g2 = guard(
        s,
        containers=lambda: states,
        watched=watched,
        exclude=frozenset({"immich_machine_learning"}),
    )
    assert g2.check() is None


def test_trip_is_latched():
    s = RollingStats(window_s=20)
    feed(s, "ping", 10, ok=False)
    g = guard(s)
    first = g.check()
    s2 = RollingStats(window_s=20)  # even if things "look fine" later, a trip stays tripped
    g.stats = s2
    assert g.check() == first
