"""Session-loop wiring tests with the experiment run and the analyzer faked out."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from shutterstorm import agent
from shutterstorm.agent import SessionConfig, run_session
from shutterstorm.chaos.journal import KillSwitch
from shutterstorm.config import Settings
from shutterstorm.planner.fallback import FallbackPlanner
from tests.test_planner import topo


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    calls = []

    def fake_run(spec, settings, out_dir, client=None, notify=None, state_dir=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        calls.append(spec)
        return {"fault": {"undone_verified": True, "outcome": "completed"}, "spec": {}}

    def fake_analyze(run_dir, baseline=None):
        spec = calls[-1]
        return {
            "experiment_id": spec.id,
            "target": spec.target,
            "fault": spec.fault,
            "outcome": "completed",
            "recovery_s": 1.0,
            "journeys_hit_as_expected": [],
            "score": {"total": 90.0},
        }

    monkeypatch.setattr(agent, "run_experiment", fake_run)
    monkeypatch.setattr(agent, "analyze_experiment", fake_analyze)
    monkeypatch.setattr(agent, "wait_until_steady", lambda *a, **k: True)
    return calls


def cfg(tmp_path, **kw):
    return SessionConfig(
        settings=Settings("http://127.0.0.1:2283", "k"),
        topology=topo(),
        planner=FallbackPlanner(),
        out_dir=tmp_path / "out",
        state_dir=tmp_path / "state",
        **kw,
    )


def test_session_runs_budget_and_adapts_to_results(fakes, tmp_path):
    res = run_session(cfg(tmp_path, max_experiments=4), client=SimpleNamespace())
    ran = [(s.target, s.fault) for s in fakes]
    assert len(ran) == 4 and len(set(ran)) == 4  # never repeats an experiment
    assert ran[0] == ("immich_server", "cpu_throttle")  # highest priority, catalog-first fault
    assert res.stopped_because == "experiment budget reached"
    assert [r["experiment_id"] for r in res.results] == ["E-01", "E-02", "E-03", "E-04"]
    assert (tmp_path / "out" / "results.json").exists()


def test_session_stops_when_planner_has_nothing_left(fakes, tmp_path):
    res = run_session(
        cfg(tmp_path, max_experiments=99, allowed_faults=("stop",)), client=SimpleNamespace()
    )
    assert len(fakes) == 4  # one 'stop' per target, then nothing
    assert res.stopped_because == "no remaining experiments"


def test_kill_switch_stops_the_session_before_the_next_experiment(fakes, tmp_path):
    KillSwitch(tmp_path / "state").trip()
    res = run_session(cfg(tmp_path, max_experiments=3), client=SimpleNamespace())
    assert fakes == []
    assert res.stopped_because == "kill switch tripped"


def test_operator_can_decline_an_experiment(fakes, tmp_path):
    asked = []

    def approve(v):
        asked.append(v.spec.id)
        return len(asked) > 1  # decline the first, accept the rest

    res = run_session(cfg(tmp_path, max_experiments=2, approve=approve), client=SimpleNamespace())
    assert res.skipped and res.skipped[0]["reason"] == "declined by operator"
    assert len(fakes) == 1


def test_unverified_undo_stops_everything_and_triggers_panic(monkeypatch, tmp_path, fakes):
    def bad_run(spec, settings, out_dir, client=None, notify=None, state_dir=None):
        out_dir.mkdir(parents=True, exist_ok=True)
        fakes.append(spec)
        return {"fault": {"undone_verified": False, "outcome": "failed"}, "spec": {}}

    panicked = []

    class FakeExecutor:
        def __init__(self, *a, **k): ...
        def panic(self, on_event=None):
            panicked.append(True)
            return []

    monkeypatch.setattr(agent, "run_experiment", bad_run)
    monkeypatch.setattr(agent, "FaultExecutor", FakeExecutor)
    res = run_session(cfg(tmp_path, max_experiments=3), client=SimpleNamespace())
    assert panicked == [True]
    assert res.stopped_because == "fault could not be verified undone"
    assert len(fakes) == 1  # no second experiment on a possibly broken system
