"""Fault + executor safety tests. Run against a throwaway nginx container, never Immich."""

from __future__ import annotations

import os
import uuid

import docker
import pytest
from docker.errors import DockerException

from shutterstorm.chaos import executor as executor_mod
from shutterstorm.chaos.executor import FaultExecutor
from shutterstorm.chaos.faults import FAULTS, FaultError, StopFault
from shutterstorm.chaos.journal import Journal
from shutterstorm.chaos.policy import PolicyError, TargetPolicy


def _client():
    try:
        c = docker.from_env()
        c.ping()
        return c
    except DockerException:
        return None


pytestmark = pytest.mark.skipif(_client() is None, reason="docker daemon not available")


@pytest.fixture
def dummy():
    client = _client()
    name = f"shutterstorm_dummy_{uuid.uuid4().hex[:6]}"
    c = client.containers.run(
        "nginx:alpine", name=name, detach=True, labels={"shutterstorm.dummy": "true"}
    )
    c.reload()
    yield c
    c.remove(force=True)


@pytest.fixture
def policy(dummy):
    return TargetPolicy(allowed=frozenset({dummy.name}), aliases={"d": dummy.name})


@pytest.fixture
def ex(policy, tmp_path, monkeypatch):
    monkeypatch.setattr(executor_mod, "UNDO_RETRY_S", 0.05)
    monkeypatch.setattr(executor_mod, "POLL_S", 0.05)
    return FaultExecutor(_client(), policy, state_dir=tmp_path)


def status_of(c):
    c.reload()
    return c.status


def test_stop_then_undo_restores_and_clears_journal(ex, dummy):
    seen = {}

    def on_event(kind, msg):
        if kind == "applied":
            seen["during"] = status_of(dummy)

    run = ex.run("d", "stop", ttl_s=1, on_event=on_event)
    assert seen["during"] == "exited"  # fault really was applied
    assert run.outcome == "completed"
    assert run.undone_verified
    assert status_of(dummy) == "running"
    assert ex.journal.entries() == []


def test_pause_then_undo(ex, dummy):
    seen = {}
    run = ex.run(
        "d",
        "pause",
        ttl_s=1,
        on_event=lambda k, m: seen.update(s=status_of(dummy)) if k == "applied" else None,
    )
    assert seen["s"] == "paused"
    assert run.undone_verified and status_of(dummy) == "running"


def test_cpu_throttle_applies_and_restores_original_limit(ex, dummy):
    seen = {}

    def on_event(kind, msg):
        if kind == "applied":
            dummy.reload()
            seen["quota"] = dummy.attrs["HostConfig"]["CpuQuota"]

    run = ex.run("d", "cpu_throttle", {"cpus": 0.5}, ttl_s=1, on_event=on_event)
    assert seen["quota"] == 50_000
    assert run.undone_verified
    dummy.reload()
    assert dummy.attrs["HostConfig"]["CpuQuota"] <= 0  # unlimited again


def test_cpu_throttle_clamps_dangerous_value():
    assert FAULTS["cpu_throttle"].validate({"cpus": 0.01}) == {"cpus": 0.25}
    assert FAULTS["cpu_throttle"].validate({"cpus": 9999}) == {"cpus": 8.0}
    with pytest.raises(FaultError):
        FAULTS["cpu_throttle"].validate({"cpus": "lots"})


def test_target_outside_allowlist_is_rejected(ex):
    with pytest.raises(PolicyError):
        ex.run("immich_server", "stop", ttl_s=1)
    assert ex.journal.entries() == []


def test_required_label_is_enforced(dummy, tmp_path):
    strict = TargetPolicy(
        allowed=frozenset({dummy.name}), required_label=("shutterstorm.target", "true")
    )
    with pytest.raises(PolicyError):
        FaultExecutor(_client(), strict, state_dir=tmp_path).run(dummy.name, "stop", ttl_s=1)
    assert status_of(dummy) == "running"


def test_unknown_fault_is_rejected(ex):
    with pytest.raises(FaultError):
        ex.run("d", "rm_rf", ttl_s=1)


def test_ttl_is_clamped_to_hard_maximum(ex, dummy):
    run = ex.run("d", "pause", ttl_s=99999, should_abort=lambda: "test")
    assert run.ttl_s == executor_mod.MAX_TTL_S
    assert run.undone_verified


def test_stop_file_aborts_early_and_still_undoes(ex, dummy):
    def on_event(kind, msg):
        if kind == "applied":
            ex.kill_switch.trip()

    run = ex.run("d", "stop", ttl_s=60, on_event=on_event)
    assert run.outcome == "aborted_manual"
    assert run.undone_verified and status_of(dummy) == "running"
    assert run.ended_at - run.started_at < 10  # nowhere near the 60 s TTL


def test_latched_kill_switch_blocks_new_faults(ex, dummy):
    ex.kill_switch.trip()
    with pytest.raises(FaultError, match="latched"):
        ex.run("d", "stop", ttl_s=1)
    assert status_of(dummy) == "running"
    ex.kill_switch.clear()
    assert ex.run("d", "pause", ttl_s=1).undone_verified


def test_guard_abort_is_reported(ex, dummy):
    calls = {"n": 0}

    def guard():
        calls["n"] += 1
        return "error rate 100% > 10%" if calls["n"] > 2 else None

    run = ex.run("d", "pause", ttl_s=60, should_abort=guard)
    assert run.outcome == "aborted_guard"
    assert "error rate" in run.reason
    assert run.undone_verified


def test_journal_is_written_before_apply(ex, dummy, monkeypatch):
    seen = {}

    class Probe(StopFault):
        def apply(self, container, params):
            seen["entries"] = Journal(ex.journal.state_dir).entries()
            raise FaultError("boom")  # fail after the check

    monkeypatch.setitem(FAULTS, "stop", Probe())
    run = ex.run("d", "stop", ttl_s=1)
    assert len(seen["entries"]) == 1  # recipe was on disk before apply() ran
    assert seen["entries"][0]["target"] == dummy.name
    assert run.outcome == "failed"
    assert run.undone_verified and ex.journal.entries() == []


def test_leftover_journal_blocks_new_faults(ex, dummy):
    ex.journal.add(dummy.name, "stop", {}, {}, 30)
    with pytest.raises(FaultError, match="panic"):
        ex.run("d", "pause", ttl_s=1)


def test_panic_restores_after_a_simulated_crash(ex, dummy):
    fault = FAULTS["stop"]
    ex.journal.add(dummy.name, "stop", {}, fault.snapshot(dummy), 30)
    dummy.stop(timeout=2)  # "tool crashed" while the fault was active
    assert status_of(dummy) == "exited"

    results = ex.panic()
    assert [r["restored"] for r in results] == [True]
    assert status_of(dummy) == "running"
    assert ex.journal.entries() == []


def test_failed_undo_is_detected_and_journal_kept(ex, dummy, monkeypatch):
    class BrokenUndo(StopFault):
        def undo(self, container, recipe):
            pass  # sabotage: never restarts the container

    monkeypatch.setitem(FAULTS, "stop", BrokenUndo())
    run = ex.run("d", "stop", ttl_s=1)
    assert run.undone_verified is False
    assert len(ex.journal.entries()) == 1  # evidence kept for panic
    assert status_of(dummy) == "exited"

    monkeypatch.setitem(FAULTS, "stop", StopFault())  # real undo available again
    assert [r["restored"] for r in ex.panic()] == [True]
    assert status_of(dummy) == "running"


def test_precheck_refuses_container_that_is_not_running(ex, dummy):
    dummy.stop(timeout=2)
    with pytest.raises(FaultError, match="not 'running'"):
        ex.run("d", "pause", ttl_s=1)
    assert ex.journal.entries() == []


def test_sigterm_during_hold_still_undoes(ex, dummy):
    import signal
    import threading

    threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    run = ex.run("d", "pause", ttl_s=30)
    assert run.outcome == "aborted_manual"
    assert run.undone_verified and status_of(dummy) == "running"
