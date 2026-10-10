"""Compute baseline and per-experiment results from a run folder.

Every number in a report comes from here. The LLM never computes or invents a figure.

Run folder inputs: probes.csv, queues.csv, uploads.csv, (events.json for experiments).
"""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Any

from shutterstorm.workload.probes import JOURNEYS, UPLOAD_NEEDS, UPLOAD_PROCESSING_NEEDS
from shutterstorm.workload.stats import percentile

RECOVERY_STABLE_S = 10.0
RECOVERY_FULL_CREDIT_S = 10.0
RECOVERY_ZERO_CREDIT_S = 120.0
LATENCY_MULTIPLE = 3.0
LATENCY_FLOOR_MS = 150.0
FAIL_ERR_RATE = 0.5

JOURNEY_NEEDS: dict[str, tuple[str, ...]] = {j.name: j.needs for j in JOURNEYS}
JOURNEY_NEEDS["upload"] = UPLOAD_NEEDS
JOURNEY_NEEDS["upload_processing"] = UPLOAD_PROCESSING_NEEDS

SCORE_WEIGHTS = {
    "availability": 0.35,
    "recovery": 0.20,
    "async_completion": 0.20,
    "integrity": 0.15,
    "blast_radius": 0.10,
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def load_probes(run_dir: Path) -> list[dict[str, Any]]:
    return [
        {
            "ts": float(r["timestamp"]),
            "journey": r["journey"],
            "ok": r["ok"] == "1",
            "status": int(r["status"]),
            "ms": float(r["latency_ms"]),
        }
        for r in _read_csv(run_dir / "probes.csv")
    ]


def load_uploads(run_dir: Path) -> list[dict[str, Any]]:
    out = []
    for r in _read_csv(run_dir / "uploads.csv"):
        out.append(
            {
                "asset_id": r["asset_id"],
                "uploaded_at": float(r["uploaded_at"]),
                "accepted": int(r["accepted_status"]) in (200, 201),
                "thumbnail_s": float(r["thumbnail_s"]) if r["thumbnail_s"] else None,
                "indexed_s": float(r["indexed_s"]) if r["indexed_s"] else None,
                "final": r["final"],
            }
        )
    return out


def _journey_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok_ms = [r["ms"] for r in rows if r["ok"]]
    n = len(rows)
    errors = sum(1 for r in rows if not r["ok"])
    return {
        "n": n,
        "errors": errors,
        "error_rate": round(errors / n, 4) if n else None,
        "p50_ms": round(percentile(ok_ms, 0.5), 1) if ok_ms else None,
        "p95_ms": round(percentile(ok_ms, 0.95), 1) if ok_ms else None,
    }


def per_journey(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    names = sorted({r["journey"] for r in rows})
    return {j: _journey_stats([r for r in rows if r["journey"] == j]) for j in names}


# ------------------------------------------------------------------ baseline
def compute_baseline(run_dir: Path, trim_s: float = 5.0) -> dict[str, Any]:
    rows = load_probes(run_dir)
    if not rows:
        raise ValueError(f"no probe data in {run_dir}")
    t0 = rows[0]["ts"] + trim_s  # drop connection warm-up
    rows = [r for r in rows if r["ts"] >= t0]
    uploads = [u for u in load_uploads(run_dir) if u["uploaded_at"] >= t0]
    queues = _read_csv(run_dir / "queues.csv")
    waiting = [int(q["waiting"]) for q in queues if q["waiting"] != ""]
    failed = [int(q["failed"]) for q in queues if q["failed"] != ""]
    done = [u for u in uploads if u["final"] == "processed"]
    return {
        "duration_s": round(rows[-1]["ts"] - rows[0]["ts"], 1),
        "journeys": per_journey(rows),
        "uploads": {
            "accepted": sum(1 for u in uploads if u["accepted"]),
            "processed": len(done),
            "median_thumbnail_s": _median([u["thumbnail_s"] for u in done]),
            "median_indexed_s": _median([u["indexed_s"] for u in done]),
        },
        "queues": {
            "max_waiting": max(waiting, default=0),
            "max_failed": max(failed, default=0),
            "idle": max(waiting, default=0) <= 2 and max(failed, default=0) == 0,
        },
    }


def _median(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(statistics.median(vals), 2) if vals else None


# --------------------------------------------------------------- experiments
def _is_bad(r: dict[str, Any], pre_p95: dict[str, float]) -> bool:
    if not r["ok"]:
        return True
    base = pre_p95.get(r["journey"])
    return base is not None and r["ms"] > max(LATENCY_MULTIPLE * base, LATENCY_FLOOR_MS)


def recovery_seconds(
    rows: list[dict[str, Any]], undone_at: float, pre_p95: dict[str, float]
) -> float | None:
    """Seconds from undo until every journey stays clean for RECOVERY_STABLE_S.

    None if the run ended before that many clean seconds were observed.
    """
    after = [r for r in rows if r["ts"] >= undone_at and r["journey"] != "upload"]
    if not after:
        return None
    last_bad = max((r["ts"] for r in after if _is_bad(r, pre_p95)), default=None)
    end = max(r["ts"] for r in after)
    if last_bad is None:
        return 0.0
    if end - last_bad < RECOVERY_STABLE_S:
        return None
    return round(max(0.0, last_bad - undone_at), 1)


def classify(stats: dict[str, Any], pre: dict[str, Any] | None) -> str:
    """healthy | degraded | fail (used to score predictions)."""
    if stats["n"] == 0:
        return "unknown"
    if stats["error_rate"] >= FAIL_ERR_RATE:
        return "fail"
    slow = (
        pre is not None
        and pre.get("p95_ms")
        and stats["p95_ms"]
        and stats["p95_ms"] > max(LATENCY_MULTIPLE * pre["p95_ms"], LATENCY_FLOOR_MS)
    )
    return "degraded" if stats["errors"] > 0 or slow else "healthy"


def analyze_experiment(run_dir: Path, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    events = json.loads((run_dir / "events.json").read_text())
    fault = events.get("fault")
    if not fault:
        raise ValueError("experiment never reached the fault stage (see events.json)")
    spec = events["spec"]
    applied, undone = fault["applied_at"], fault["undone_at"]
    target = events["target"]

    rows = load_probes(run_dir)
    uploads = load_uploads(run_dir)
    pre_rows = [r for r in rows if r["ts"] < applied]
    during_rows = [r for r in rows if applied <= r["ts"] <= undone]
    pre = per_journey(pre_rows)
    during = per_journey(during_rows)
    pre_p95 = {j: s["p95_ms"] for j, s in pre.items() if s["p95_ms"] is not None}
    if baseline:
        pre_p95.update(
            {j: s["p95_ms"] for j, s in baseline["journeys"].items() if s["p95_ms"] is not None}
        )

    rec = recovery_seconds(rows, undone, pre_p95)
    # "after" = the steady state once recovery is confirmed; None if it never recovered
    # rec is the offset of the LAST bad sample, so the clean window starts strictly after it
    after_start = (undone + rec + (1e-6 if rec else 0.0)) if rec is not None else None
    after = (
        per_journey([r for r in rows if r["ts"] >= after_start and r["journey"] != "upload"])
        if after_start is not None
        else {}
    )
    journeys = {
        j: {
            "pre": pre.get(j),
            "during": during.get(j),
            "after": after.get(j),
            "observed": classify(during[j], pre.get(j)) if j in during else "unknown",
        }
        for j in sorted(set(pre) | set(during))
    }
    # Derived journey: of the uploads accepted while the fault was active, how many got fully
    # processed (thumbnail AND search index)? Accepted-but-never-processed is the headline failure.
    fault_uploads = [u for u in uploads if applied <= u["uploaded_at"] <= undone and u["accepted"]]
    if fault_uploads:
        n = len(fault_uploads)
        done = sum(1 for u in fault_uploads if u["final"] == "processed")
        journeys["upload_processing"] = {
            "pre": None,
            "during": {
                "n": n,
                "errors": n - done,
                "error_rate": round((n - done) / n, 4),
                "p50_ms": None,
                "p95_ms": None,
            },
            "observed": "healthy" if done == n else ("fail" if done == 0 else "degraded"),
            "after": None,
        }
    failing = [j for j, v in journeys.items() if v["observed"] in ("fail", "degraded")]
    depends = {j for j, needs in JOURNEY_NEEDS.items() if target in needs}
    expected_hit = sorted(set(failing) & depends)
    collateral = sorted(set(failing) - depends)

    # Uploads made while the fault was active.
    window = [u for u in uploads if applied <= u["uploaded_at"] <= undone and u["accepted"]]
    all_accepted = [u for u in uploads if u["accepted"]]
    processed_in_window = [u for u in window if u["final"] == "processed"]
    async_block = {
        "accepted_total": len(all_accepted),
        "processed_total": sum(1 for u in all_accepted if u["final"] == "processed"),
        "accepted_during_fault": len(window),
        "processed_during_fault": len(processed_in_window),
        "thumbnail_ok_during_fault": sum(1 for u in window if u["thumbnail_s"] is not None),
        "indexed_ok_during_fault": sum(1 for u in window if u["indexed_s"] is not None),
        "never_indexed_during_fault": sum(1 for u in window if u["indexed_s"] is None),
    }

    queues = _read_csv(run_dir / "queues.csv")
    queue_peak: dict[str, dict[str, int]] = {}
    for q in queues:
        if q["waiting"] == "":
            continue
        peak = queue_peak.setdefault(q["queue"], {"waiting": 0, "active": 0, "failed": 0})
        for k in peak:
            peak[k] = max(peak[k], int(q[k]))

    predictions = _score_predictions(spec.get("predictions", []), journeys)
    score = _score(
        journeys=journeys,
        recovery_s=rec,
        async_block=async_block,
        undone_verified=fault["undone_verified"],
        collateral=collateral,
        not_dependent=[j for j in journeys if j not in depends],
    )

    return {
        "experiment_id": spec["id"],
        "target": target,
        "fault": fault["fault"],
        "params": fault["params"],
        "planner": spec.get("planner", "manual"),
        "hypothesis": spec.get("hypothesis", ""),
        "outcome": fault["outcome"],
        "abort_reason": fault["reason"] if fault["outcome"] != "completed" else None,
        "fault_duration_s": round(undone - applied, 1),
        "undone_verified": fault["undone_verified"],
        "journeys": journeys,
        "journeys_hit_as_expected": expected_hit,
        "journeys_hit_unexpectedly": collateral,
        "recovery_s": rec,
        "recovered": rec is not None,
        "after": _after_verdict(journeys, pre_p95, rec),
        "async": async_block,
        "queue_peak": queue_peak,
        "predictions": predictions,
        "score": score,
    }


def _score_predictions(preds: list[dict[str, Any]], journeys: dict[str, Any]) -> dict[str, Any]:
    scored = []
    for p in preds:
        observed = journeys.get(p["journey"], {}).get("observed", "unknown")
        scored.append(
            {
                "journey": p["journey"],
                "expected": p["expected"],
                "observed": observed,
                "correct": p["expected"] == observed,
                "confidence": p.get("confidence"),
            }
        )
    n = len(scored)
    return {
        "items": scored,
        "accuracy": round(sum(s["correct"] for s in scored) / n, 3) if n else None,
    }


def _score(
    journeys: dict[str, Any],
    recovery_s: float | None,
    async_block: dict[str, Any],
    undone_verified: bool,
    collateral: list[str],
    not_dependent: list[str],
) -> dict[str, Any]:
    """Resilience score 0..100, formula from CLAUDE.md section 4.8. Heuristic, documented."""
    # availability = request success; processing is scored separately (async_completion)
    rates = [
        v["during"]["error_rate"]
        for j, v in journeys.items()
        if v["during"] and v["during"]["n"] and j != "upload_processing"
    ]
    availability = 1 - (sum(rates) / len(rates)) if rates else 1.0

    if recovery_s is None:
        recovery = 0.0
    elif recovery_s <= RECOVERY_FULL_CREDIT_S:
        recovery = 1.0
    else:
        span = RECOVERY_ZERO_CREDIT_S - RECOVERY_FULL_CREDIT_S
        recovery = max(0.0, 1 - (recovery_s - RECOVERY_FULL_CREDIT_S) / span)

    # Share of upload ATTEMPTS during the fault that ended fully processed. Attempts that were
    # refused count against it: scoring only accepted uploads would reward an outage that rejects
    # every upload. No attempts at all means "not measured" (full credit, flagged as such).
    upload_during = (journeys.get("upload") or {}).get("during") or {}
    attempts = max(upload_during.get("n", 0), async_block["accepted_during_fault"])
    async_completion = async_block["processed_during_fault"] / attempts if attempts else 1.0
    n_win = attempts

    integrity = (
        1.0 if (undone_verified and recovery_s is not None) else (0.5 if undone_verified else 0.0)
    )
    blast = 1 - (len(collateral) / len(not_dependent)) if not_dependent else 1.0

    parts = {
        "availability": availability,
        "recovery": recovery,
        "async_completion": async_completion,
        "integrity": integrity,
        "blast_radius": blast,
    }
    total = 100 * sum(SCORE_WEIGHTS[k] * v for k, v in parts.items())
    return {
        "total": round(total, 1),
        "parts": {k: round(v, 3) for k, v in parts.items()},
        "weights": SCORE_WEIGHTS,
        "async_applicable": bool(n_win),
        "upload_attempts_during_fault": n_win,
    }


def _after_verdict(
    journeys: dict[str, Any], pre_p95: dict[str, float], rec: float | None
) -> dict[str, Any]:
    """Is every journey back to its pre-fault behaviour once recovery is confirmed?"""
    if rec is None:
        return {"measured": False, "all_back_to_baseline": False, "not_back": []}
    not_back = []
    for j, v in journeys.items():
        a = v.get("after")
        if not a or not a["n"]:
            continue
        limit = (
            max(LATENCY_MULTIPLE * pre_p95.get(j, 0), LATENCY_FLOOR_MS) if pre_p95.get(j) else None
        )
        slow = limit is not None and a["p95_ms"] is not None and a["p95_ms"] > limit
        if a["errors"] > 0 or slow:
            not_back.append(j)
    return {"measured": True, "all_back_to_baseline": not not_back, "not_back": not_back}
