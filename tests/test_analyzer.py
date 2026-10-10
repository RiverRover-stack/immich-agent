"""Analyzer tests on synthetic run folders where the correct answer is known by construction."""

import csv
import json

import pytest

from shutterstorm.analyzer.metrics import (
    analyze_experiment,
    compute_baseline,
    recovery_seconds,
)

T0 = 1000.0
APPLIED, UNDONE, END = T0 + 20, T0 + 50, T0 + 90  # 30 s fault, 40 s of tail


def write_csv(path, header, rows):
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def make_run(tmp_path, search_bad_until=None, predictions=None):
    """ML stopped T0+20..T0+50. search fails during the fault (and until `search_bad_until`)."""
    bad_until = search_bad_until if search_bad_until is not None else UNDONE
    probes = []
    for t in range(90):
        ts = T0 + t
        probes.append([f"{ts:.3f}", "ping", 1, 200, 4.0])
        probes.append([f"{ts:.3f}", "browse", 1, 200, 12.0])
        if t % 2 == 0:
            bad = APPLIED <= ts <= bad_until
            probes.append([f"{ts:.3f}", "search", 0 if bad else 1, 500 if bad else 200, 30.0])
    write_csv(
        tmp_path / "probes.csv", ["timestamp", "journey", "ok", "status", "latency_ms"], probes
    )

    uploads = []
    for i, off in enumerate([5, 15, 25, 35, 45, 60, 70]):
        in_fault = APPLIED <= T0 + off <= UNDONE
        uploads.append(
            [
                f"id{i}",
                f"{T0 + off:.3f}",
                201,
                20.0,
                1.0,
                "" if in_fault else 1.0,
                "pending" if in_fault else "processed",
            ]
        )
    write_csv(
        tmp_path / "uploads.csv",
        [
            "asset_id",
            "uploaded_at",
            "accepted_status",
            "upload_ms",
            "thumbnail_s",
            "indexed_s",
            "final",
        ],
        uploads,
    )
    write_csv(
        tmp_path / "queues.csv",
        ["timestamp", "queue", "active", "waiting", "failed"],
        [[f"{T0 + 1:.3f}", "smartSearch", 0, 0, 0], [f"{T0 + 30:.3f}", "smartSearch", 1, 4, 0]],
    )
    events = {
        "spec": {
            "id": "E-01",
            "planner": "fallback",
            "hypothesis": "search fails",
            "predictions": predictions or [],
        },
        "target": "immich_machine_learning",
        "fault": {
            "fault": "stop",
            "params": {},
            "outcome": "completed",
            "reason": "ttl",
            "applied_at": APPLIED,
            "undone_at": UNDONE,
            "undone_verified": True,
        },
    }
    (tmp_path / "events.json").write_text(json.dumps(events))
    return tmp_path


def test_search_fails_during_fault_while_unrelated_journeys_stay_healthy(tmp_path):
    r = analyze_experiment(make_run(tmp_path))
    assert r["journeys"]["search"]["observed"] == "fail"
    assert r["journeys"]["search"]["during"]["error_rate"] == 1.0
    assert r["journeys"]["ping"]["observed"] == "healthy"
    assert r["journeys"]["browse"]["observed"] == "healthy"
    # search depends on ML, and so does background processing of uploads
    assert r["journeys_hit_as_expected"] == ["search", "upload_processing"]
    assert r["journeys"]["upload_processing"]["observed"] == "fail"  # 0 of 3 fully processed
    assert r["journeys_hit_unexpectedly"] == []  # no collateral damage


def test_recovery_is_measured_from_undo_to_last_bad_sample(tmp_path):
    r = analyze_experiment(make_run(tmp_path, search_bad_until=UNDONE + 14))
    assert r["recovery_s"] == pytest.approx(14.0, abs=1.0)
    assert r["recovered"] is True


def test_instant_recovery_is_zero(tmp_path):
    assert analyze_experiment(make_run(tmp_path))["recovery_s"] == 0.0


def test_not_recovered_when_run_ends_before_ten_clean_seconds():
    rows = [
        {"ts": 100.0 + i, "journey": "search", "ok": i > 8, "status": 200, "ms": 5.0}
        for i in range(10)
    ]
    # last bad sample at t=107; run ends at t=109 -> only 2 clean seconds observed
    assert recovery_seconds(rows, undone_at=100.0, pre_p95={}) is None


def test_accepted_but_never_processed_is_counted(tmp_path):
    a = analyze_experiment(make_run(tmp_path))["async"]
    assert a["accepted_during_fault"] == 3  # uploads at +25, +35, +45
    assert a["thumbnail_ok_during_fault"] == 3  # thumbnails still worked
    assert a["never_indexed_during_fault"] == 3  # ...but search indexing never happened
    assert a["processed_during_fault"] == 0


def test_queue_peak_is_recorded(tmp_path):
    q = analyze_experiment(make_run(tmp_path))["queue_peak"]
    assert q["smartSearch"] == {"waiting": 4, "active": 1, "failed": 0}


def test_prediction_accuracy_is_scored_against_observation(tmp_path):
    preds = [
        {"journey": "search", "expected": "fail", "confidence": 0.9},
        {"journey": "ping", "expected": "healthy", "confidence": 0.8},
        {"journey": "browse", "expected": "fail", "confidence": 0.3},  # wrong on purpose
    ]
    r = analyze_experiment(make_run(tmp_path, predictions=preds))
    assert r["predictions"]["accuracy"] == pytest.approx(2 / 3, abs=0.001)
    assert [i["correct"] for i in r["predictions"]["items"]] == [True, True, False]


def test_score_is_bounded_and_penalises_stalled_processing(tmp_path):
    r = analyze_experiment(make_run(tmp_path))
    s = r["score"]
    assert 0 <= s["total"] <= 100
    assert s["parts"]["async_completion"] == 0.0  # 0 of 3 uploads fully processed
    assert s["parts"]["blast_radius"] == 1.0  # nothing unrelated broke
    assert s["parts"]["recovery"] == 1.0
    assert sum(s["weights"].values()) == pytest.approx(1.0)


def test_score_drops_when_unrelated_journey_breaks(tmp_path):
    run = make_run(tmp_path)
    rows = list(csv.reader((run / "probes.csv").open()))
    for row in rows[1:]:
        if row[1] == "browse" and APPLIED <= float(row[0]) <= UNDONE:
            row[2], row[3] = "0", "0"  # browse dies too: collateral damage
    write_csv(run / "probes.csv", rows[0], rows[1:])
    r = analyze_experiment(run)
    assert "browse" in r["journeys_hit_unexpectedly"]
    assert r["score"]["parts"]["blast_radius"] < 1.0


def test_baseline_summarises_steady_state(tmp_path):
    run = make_run(tmp_path)
    (run / "events.json").unlink()
    b = compute_baseline(run, trim_s=0)
    assert b["journeys"]["ping"]["error_rate"] == 0.0
    assert b["journeys"]["ping"]["p95_ms"] == 4.0
    assert b["queues"]["max_waiting"] == 4
    assert b["uploads"]["accepted"] == 7


def test_after_window_confirms_return_to_baseline(tmp_path):
    r = analyze_experiment(make_run(tmp_path, search_bad_until=UNDONE + 6))
    assert r["after"]["measured"] is True
    assert r["after"]["all_back_to_baseline"] is True
    assert r["journeys"]["search"]["after"]["errors"] == 0
    assert r["journeys"]["search"]["during"]["error_rate"] == 1.0  # before/during/after all present
    assert r["journeys"]["search"]["pre"]["error_rate"] == 0.0


def test_after_is_not_measured_when_system_never_recovered(tmp_path):
    r = analyze_experiment(make_run(tmp_path, search_bad_until=END))  # still failing at run end
    assert r["recovered"] is False
    assert r["after"] == {"measured": False, "all_back_to_baseline": False, "not_back": []}
    assert r["journeys"]["search"]["after"] is None


def test_refused_uploads_during_the_fault_are_not_scored_as_full_credit(tmp_path):
    """Regression: no ACCEPTED upload in the window used to mean async completion = 1.0."""
    run = make_run(tmp_path)
    # drop every accepted upload in the fault window and record two refused attempts instead
    ups = list(csv.reader((run / "uploads.csv").open()))
    keep = [ups[0]] + [u for u in ups[1:] if not (APPLIED <= float(u[1]) <= UNDONE)]
    write_csv(run / "uploads.csv", keep[0], keep[1:])
    probes = list(csv.reader((run / "probes.csv").open()))
    probes += [
        [f"{APPLIED + 5:.3f}", "upload", "0", "0", "5000.0"],
        [f"{APPLIED + 15:.3f}", "upload", "0", "500", "30.0"],
    ]
    write_csv(run / "probes.csv", probes[0], probes[1:])

    r = analyze_experiment(run)
    assert r["async"]["accepted_during_fault"] == 0
    assert r["score"]["upload_attempts_during_fault"] == 2
    assert r["score"]["parts"]["async_completion"] == 0.0
    assert r["journeys"]["upload"]["observed"] == "fail"


def test_no_upload_attempts_at_all_is_flagged_not_applicable(tmp_path):
    run = make_run(tmp_path)
    ups = list(csv.reader((run / "uploads.csv").open()))
    write_csv(
        run / "uploads.csv", ups[0], [u for u in ups[1:] if not (APPLIED <= float(u[1]) <= UNDONE)]
    )
    r = analyze_experiment(run)
    assert r["score"]["async_applicable"] is False
