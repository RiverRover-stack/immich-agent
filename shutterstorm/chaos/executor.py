"""Fault executor: the one safe path by which any fault (manual or agent-chosen) is applied.

Order is the safety property:
    kill switch clear -> no leftover faults -> allowlist -> clamp -> precheck -> snapshot
    -> WRITE JOURNAL -> apply -> hold (TTL / STOP file / guard / Ctrl-C) -> undo -> verify -> clear journal
"""

from __future__ import annotations

import signal
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import docker
from docker.errors import DockerException, NotFound

from shutterstorm.chaos.faults import FaultError, get_fault
from shutterstorm.chaos.journal import Journal, KillSwitch
from shutterstorm.chaos.policy import TargetPolicy

MAX_TTL_S = 120.0
DEFAULT_TTL_S = 30.0
POLL_S = 0.25
UNDO_ATTEMPTS = 3
UNDO_RETRY_S = 1.0

Event = Callable[[str, str], None]
AbortCheck = Callable[[], str | None]


@dataclass
class FaultRun:
    id: str
    target: str
    fault: str
    params: dict[str, Any]
    ttl_s: float
    started_at: float
    ended_at: float = 0.0
    outcome: str = "failed"  # completed | aborted_manual | aborted_guard | failed
    reason: str = ""
    applied_at: float = 0.0
    undone_at: float = 0.0
    undone_verified: bool = False
    notes: list[str] = field(default_factory=list)


class FaultExecutor:
    def __init__(
        self,
        client: docker.DockerClient,
        policy: TargetPolicy,
        state_dir: Path | None = None,
    ) -> None:
        self.client = client
        self.policy = policy
        self.journal = Journal(state_dir)
        self.kill_switch = KillSwitch(state_dir)

    # ------------------------------------------------------------------ run
    def run(
        self,
        target: str,
        fault_name: str,
        params: dict[str, Any] | None = None,
        ttl_s: float = DEFAULT_TTL_S,
        should_abort: AbortCheck | None = None,
        on_event: Event | None = None,
    ) -> FaultRun:
        emit: Event = on_event or (lambda kind, msg: None)

        if self.kill_switch.is_set():
            raise FaultError(
                f"kill switch is latched ({self.kill_switch.path}). Clear it with: shutterstorm resume"
            )
        leftover = self.journal.entries()
        if leftover:
            raise FaultError(
                f"{len(leftover)} fault(s) from a previous run are not undone. "
                "Run: shutterstorm panic"
            )

        name = self.policy.resolve(target)
        fault = get_fault(fault_name)
        clean = fault.validate(params or {})
        ttl = min(MAX_TTL_S, max(1.0, float(ttl_s)))
        if ttl != ttl_s:
            emit("clamp", f"ttl {ttl_s}s -> {ttl:g}s (allowed 1..{MAX_TTL_S:g})")

        try:
            container = self.client.containers.get(name)
        except NotFound:
            raise FaultError(f"container {name!r} does not exist (is Immich up?)") from None
        self.policy.check_container(name, container.labels or {})
        fault.precheck(container)
        recipe = fault.snapshot(container)

        entry = self.journal.add(name, fault.name, clean, recipe, ttl)  # BEFORE apply
        run = FaultRun(entry["id"], name, fault.name, clean, ttl, started_at=time.time())
        emit("journal", f"undo recipe saved ({entry['id']})")

        restore_signal = self._install_sigterm_handler()
        try:
            try:
                fault.apply(container, clean)
                run.applied_at = time.time()
                emit("applied", f"{fault.name} on {name} for up to {ttl:g}s")
                run.outcome, run.reason = self._hold(ttl, should_abort, emit)
            except KeyboardInterrupt:
                run.outcome, run.reason = "aborted_manual", "interrupted (Ctrl-C / SIGTERM)"
            except (DockerException, FaultError) as e:
                run.outcome, run.reason = "failed", f"apply failed: {e}"
            finally:
                emit("undo", "undoing fault")
                run.undone_verified = self._undo_and_verify(fault, container, recipe, run)
                run.undone_at = time.time()
        finally:
            restore_signal()

        if run.undone_verified:
            self.journal.remove(entry["id"])
            emit("verified", f"{name} restored; journal cleared")
        else:
            run.notes.append("undo NOT verified: journal entry kept; run `shutterstorm panic`")
            emit("error", run.notes[-1])
        run.ended_at = time.time()
        return run

    def _hold(self, ttl: float, should_abort: AbortCheck | None, emit: Event) -> tuple[str, str]:
        deadline = time.monotonic() + ttl
        while time.monotonic() < deadline:
            if self.kill_switch.is_set():
                emit("abort", "STOP file detected")
                return "aborted_manual", "STOP file"
            if should_abort is not None:
                reason = should_abort()
                if reason:
                    emit("abort", f"guard: {reason}")
                    return "aborted_guard", reason
            time.sleep(POLL_S)
        return "completed", f"ttl {ttl:g}s elapsed"

    def _undo_and_verify(self, fault: Any, container: Any, recipe: dict, run: FaultRun) -> bool:
        for attempt in range(1, UNDO_ATTEMPTS + 1):
            try:
                fault.undo(container, recipe)
                if fault.verify_undone(container, recipe):
                    return True
                run.notes.append(f"verify failed (attempt {attempt})")
            except (DockerException, FaultError) as e:
                run.notes.append(f"undo error (attempt {attempt}): {e}")
            time.sleep(UNDO_RETRY_S)
        return False

    @staticmethod
    def _install_sigterm_handler() -> Callable[[], None]:
        """Turn SIGTERM into KeyboardInterrupt so `kill <pid>` still undoes the fault."""
        if threading.current_thread() is not threading.main_thread():
            return lambda: None

        def handler(signum: int, frame: Any) -> None:
            raise KeyboardInterrupt

        previous = signal.signal(signal.SIGTERM, handler)
        return lambda: signal.signal(signal.SIGTERM, previous)

    # ---------------------------------------------------------------- panic
    def panic(self, on_event: Event | None = None) -> list[dict[str, Any]]:
        """Undo everything still in the journal (crash recovery). Returns one result per entry."""
        emit: Event = on_event or (lambda kind, msg: None)
        results: list[dict[str, Any]] = []
        for entry in self.journal.entries():
            result = {"id": entry["id"], "target": entry["target"], "fault": entry["fault"]}
            try:
                fault = get_fault(entry["fault"])
                container = self.client.containers.get(entry["target"])
                scratch = FaultRun(entry["id"], entry["target"], entry["fault"], {}, 0, 0.0)
                ok = self._undo_and_verify(fault, container, entry["recipe"], scratch)
                result["restored"] = ok
                result["notes"] = scratch.notes
            except NotFound:
                result.update(restored=False, notes=["container no longer exists"])
            except (DockerException, FaultError) as e:
                result.update(restored=False, notes=[str(e)])
            if result["restored"]:
                self.journal.remove(entry["id"])
            emit("panic", f"{entry['target']}: {'restored' if result['restored'] else 'FAILED'}")
            results.append(result)
        return results
