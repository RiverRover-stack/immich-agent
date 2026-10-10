"""Build topology.json: services, dependencies, journeys and a static configuration audit.

Sources (all read-only, nothing is guessed):
  1. the pinned Immich docker-compose.yml        -> services, depends_on, volumes, restart, limits
  2. docker inspect (optional, if the daemon is up) -> what is actually running and limited
  3. journeys.yaml (hand-maintained)             -> what users lose when a component stops
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from docker.errors import DockerException

from shutterstorm.config import ROOT

DEFAULT_COMPOSE = ROOT / "immich" / "docker-compose.yml"
DEFAULT_JOURNEYS = ROOT / "journeys.yaml"

# Services that hold state we cannot trivially recreate. A bind-mounted data directory or a
# database/queue image is the signal; this list is only a fallback label.
STATEFUL_HINTS = ("postgres", "valkey", "redis")


def _as_list(depends_on: Any) -> list[str]:
    if not depends_on:
        return []
    if isinstance(depends_on, dict):
        return list(depends_on)
    return list(depends_on)


def load_compose(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or "services" not in data:
        raise ValueError(f"{path} does not look like a compose file (no 'services')")
    return data


def _limits(svc: dict[str, Any]) -> dict[str, Any]:
    res = (svc.get("deploy") or {}).get("resources", {}).get("limits", {})
    return {
        "memory": svc.get("mem_limit") or res.get("memory"),
        "cpus": svc.get("cpus") or res.get("cpus"),
    }


def _has_healthcheck(svc: dict[str, Any]) -> bool:
    hc = svc.get("healthcheck")
    if hc is None:
        return False  # image may define one; runtime inspection fills this in
    return not hc.get("disable", False)


def build_nodes(compose: dict[str, Any], runtime: dict[str, dict] | None) -> list[dict[str, Any]]:
    nodes = []
    for sid, svc in compose["services"].items():
        name = svc.get("container_name", sid)
        image = svc.get("image", "")
        volumes = svc.get("volumes", []) or []
        data_mounts = [v for v in volumes if isinstance(v, str) and not v.startswith("/etc/")]
        stateful = any(h in (image + sid).lower() for h in STATEFUL_HINTS) or any(
            "/var/lib/" in v or ":/data" in v for v in data_mounts
        )
        node = {
            "id": name,
            "service": sid,
            "image": image.split("@")[0],
            "kind": "stateful" if stateful else "stateless",
            "replicas": int((svc.get("deploy") or {}).get("replicas", 1)),
            "restart_policy": svc.get("restart", "no"),
            "has_healthcheck": _has_healthcheck(svc),
            "limits": _limits(svc),
            "ports": [str(p) for p in svc.get("ports", []) or []],
            "volumes": [str(v) for v in volumes],
            "depends_on": [
                compose["services"].get(d, {}).get("container_name", d)
                for d in _as_list(svc.get("depends_on"))
            ],
        }
        if runtime and name in runtime:
            node["runtime"] = runtime[name]
        nodes.append(node)
    return nodes


def build_edges(
    nodes: list[dict[str, Any]], extra_edges: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    edges = []
    for n in nodes:
        for dep in n["depends_on"]:
            edges.append(
                {
                    "from": n["id"],
                    "to": dep,
                    "kind": "declared",
                    "protocol": None,
                    "confidence": "high",
                    "evidence": [f"compose: {n['service']}.depends_on lists it"],
                }
            )
    for e in extra_edges:
        edges.append(
            {
                "from": e["from"],
                "to": e["to"],
                "kind": "implicit",
                "protocol": e.get("protocol"),
                "port": e.get("port"),
                "confidence": e.get("confidence", "medium"),
                "evidence": [" ".join(str(e.get("evidence", "")).split())],
            }
        )
    return edges


def audit(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Static findings derived only from configuration (no chaos needed), grouped by issue.

    One finding per kind of problem, listing every affected service, so a report says
    "no resource limits on any service" once rather than once per container.
    """
    findings: list[dict[str, Any]] = []

    def add(severity: str, services: list[str], title: str, evidence: list[str]) -> None:
        findings.append(
            {
                "severity": severity,
                "services": sorted(services),
                "title": title,
                "evidence": evidence,
                "source": "static configuration",
            }
        )

    declared = {(e["from"], e["to"]) for e in edges if e["kind"] == "declared"}
    for e in edges:
        if e["kind"] == "implicit" and (e["from"], e["to"]) not in declared:
            add(
                "high",
                [e["from"], e["to"]],
                f"Hidden dependency: {e['from']} needs {e['to']} but does not declare it",
                [
                    "compose depends_on does not list it, so start order and health are not coupled",
                    *e["evidence"],
                ],
            )

    def runtime_unlimited(n: dict[str, Any]) -> bool:
        rt = n.get("runtime", {})
        return rt.get("memory_limit_bytes", 0) == 0 and not rt.get("cpu_quota", 0) > 0

    unlimited = [
        n["id"]
        for n in nodes
        if not n["limits"]["memory"] and not n["limits"]["cpus"] and runtime_unlimited(n)
    ]
    if unlimited:
        add(
            "medium",
            unlimited,
            "No CPU or memory limits: one container can starve the others",
            ["compose sets no mem_limit / cpus / deploy.resources.limits", "runtime: unlimited"],
        )

    stateful = [n["id"] for n in nodes if n["replicas"] == 1 and n["kind"] == "stateful"]
    stateless = [n["id"] for n in nodes if n["replicas"] == 1 and n["kind"] == "stateless"]
    if stateful:
        add(
            "high",
            stateful,
            "Single instance of stateful services: any failure is an outage with no failover",
            ["compose defines one container each and no replicas"],
        )
    if stateless:
        add(
            "medium",
            stateless,
            "Single instance of stateless services: no redundancy",
            ["compose defines one container each and no replicas"],
        )

    restart_only = [
        n["id"]
        for n in nodes
        if n["restart_policy"] in ("always", "unless-stopped") and n["has_healthcheck"]
    ]
    if restart_only:
        add(
            "medium",
            restart_only,
            "Restart policy reacts to exit only: a hung but running process is never restarted",
            [
                "restart: always together with a healthcheck",
                "Docker does not restart a container for being 'unhealthy'",
            ],
        )

    no_hc = [
        n["id"]
        for n in nodes
        if not n["has_healthcheck"] and not n.get("runtime", {}).get("health")
    ]
    if no_hc:
        add(
            "low",
            no_hc,
            "No healthcheck: failures are only visible when the process exits",
            ["none defined"],
        )

    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["title"]))
    for i, f in enumerate(findings, 1):
        f["id"] = f"A-{i:02d}"
    return findings


def inspect_runtime(client: Any, names: list[str]) -> dict[str, dict]:
    """Facts about the live containers. Returns {} for anything unreachable."""
    out: dict[str, dict] = {}
    for n in names:
        try:
            c = client.containers.get(n)
        except DockerException:
            out[n] = {"status": "missing"}  # absent, or the daemon is unreachable
            continue
        host = c.attrs.get("HostConfig", {})
        state = c.attrs.get("State", {})
        out[n] = {
            "status": c.status,
            "health": (state.get("Health") or {}).get("Status"),
            "restart_count": c.attrs.get("RestartCount", 0),
            "restart_policy_runtime": (host.get("RestartPolicy") or {}).get("Name"),
            "memory_limit_bytes": host.get("Memory", 0),
            "cpu_quota": host.get("CpuQuota", 0),
            "image_id": c.attrs.get("Image", "")[:19],
        }
    return out


def build_topology(
    compose_path: Path = DEFAULT_COMPOSE,
    journeys_path: Path = DEFAULT_JOURNEYS,
    docker_client: Any = None,
) -> dict[str, Any]:
    compose = load_compose(compose_path)
    names = [s.get("container_name", sid) for sid, s in compose["services"].items()]
    runtime = inspect_runtime(docker_client, names) if docker_client else None
    jdata = yaml.safe_load(journeys_path.read_text()) or {}

    nodes = build_nodes(compose, runtime)
    edges = build_edges(nodes, jdata.get("extra_edges", []))
    journeys = [
        {
            "name": name,
            "needs": j["needs"],
            "confidence": j.get("confidence", "medium"),
            "description": j.get("description", ""),
            "evidence": " ".join(str(j.get("evidence", "")).split()),
        }
        for name, j in (jdata.get("journeys") or {}).items()
    ]
    unknown = {
        need for j in journeys for need in j["needs"] if need not in {n["id"] for n in nodes}
    }
    if unknown:
        raise ValueError(f"journeys.yaml references unknown components: {sorted(unknown)}")

    return {
        "app": "immich",
        "compose_file": str(compose_path),
        "nodes": nodes,
        "edges": edges,
        "journeys": journeys,
        "audit": audit(nodes, edges),
    }
