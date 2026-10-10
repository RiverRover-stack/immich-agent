"""Fault library. Every fault is reversible and can prove that it was reversed.

Contract (per fault):
    validate(params)         -> clamped params (never trust the caller)
    precheck(container)      -> raise FaultError if the target is not in a state we can restore
    snapshot(container)      -> recipe: whatever undo() needs, captured BEFORE apply()
    apply(container, params)
    undo(container, recipe)  -> idempotent; safe to call even if apply() only half-ran
    verify_undone(container, recipe) -> True only if the original state is really back

"Undone" means the container is back in its original *state* (running, unpaused, unlimited).
Whether the service has *recovered* (healthy, serving users) is a measurement, taken by probes.
"""

from __future__ import annotations

from typing import Any, ClassVar

MIN_CPUS = 0.25
MAX_CPUS = 8.0
CPU_PERIOD_US = 100_000
STOP_GRACE_S = 30  # let databases shut down cleanly instead of being killed


class FaultError(Exception):
    """A fault cannot be applied, or could not be reversed."""


class Fault:
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    def precheck(self, container: Any) -> None:
        container.reload()
        if container.status != "running":
            raise FaultError(
                f"{container.name} is {container.status!r}, not 'running'; "
                "refusing to fault a container we could not restore to its original state"
            )

    def snapshot(self, container: Any) -> dict[str, Any]:
        return {}

    def apply(self, container: Any, params: dict[str, Any]) -> None:
        raise NotImplementedError

    def undo(self, container: Any, recipe: dict[str, Any]) -> None:
        raise NotImplementedError

    def verify_undone(self, container: Any, recipe: dict[str, Any]) -> bool:
        raise NotImplementedError


class StopFault(Fault):
    name = "stop"
    description = "Crash: stop the container (docker stop); start it again on undo"

    def apply(self, container: Any, params: dict[str, Any]) -> None:
        container.stop(timeout=STOP_GRACE_S)  # SIGTERM first; SIGKILL only if stuck

    def undo(self, container: Any, recipe: dict[str, Any]) -> None:
        container.reload()
        if container.status != "running":
            container.start()

    def verify_undone(self, container: Any, recipe: dict[str, Any]) -> bool:
        container.reload()
        return container.status == "running"


class PauseFault(Fault):
    name = "pause"
    description = "Hang: freeze all processes (docker pause); alive but unresponsive"

    def apply(self, container: Any, params: dict[str, Any]) -> None:
        container.pause()

    def undo(self, container: Any, recipe: dict[str, Any]) -> None:
        container.reload()
        if container.status == "paused":
            container.unpause()

    def verify_undone(self, container: Any, recipe: dict[str, Any]) -> bool:
        container.reload()
        return container.status == "running"


class CpuThrottleFault(Fault):
    name = "cpu_throttle"
    description = "Starvation: cap the container's CPU (docker update); original is restored"

    def validate(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            cpus = float(params.get("cpus", 1.0))
        except (TypeError, ValueError) as e:
            raise FaultError(f"cpus must be a number, got {params.get('cpus')!r}") from e
        return {"cpus": min(MAX_CPUS, max(MIN_CPUS, cpus))}

    def snapshot(self, container: Any) -> dict[str, Any]:
        host = container.attrs.get("HostConfig", {})
        return {
            "cpu_quota": int(host.get("CpuQuota") or 0),
            "cpu_period": int(host.get("CpuPeriod") or 0),
        }

    def apply(self, container: Any, params: dict[str, Any]) -> None:
        container.update(cpu_period=CPU_PERIOD_US, cpu_quota=int(params["cpus"] * CPU_PERIOD_US))

    def undo(self, container: Any, recipe: dict[str, Any]) -> None:
        quota = recipe["cpu_quota"]
        # Reading 0 means "unlimited", but the API only clears a limit when given -1.
        container.update(cpu_quota=quota if quota > 0 else -1)

    def verify_undone(self, container: Any, recipe: dict[str, Any]) -> bool:
        container.reload()
        now = int(container.attrs["HostConfig"].get("CpuQuota") or 0)
        was = recipe["cpu_quota"]
        return (now <= 0) if was <= 0 else (now == was)


FAULTS: dict[str, Fault] = {f.name: f for f in (StopFault(), PauseFault(), CpuThrottleFault())}


def get_fault(name: str) -> Fault:
    try:
        return FAULTS[name]
    except KeyError:
        known = ", ".join(sorted(FAULTS))
        raise FaultError(f"unknown fault {name!r}. Available: {known}") from None
