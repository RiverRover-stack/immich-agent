from pathlib import Path

import pytest
import yaml

from shutterstorm.analyzer.metrics import JOURNEY_NEEDS
from shutterstorm.discover.topology import DEFAULT_JOURNEYS, audit, build_topology, load_compose

COMPOSE = """
name: app
services:
  web:
    container_name: app_web
    image: example/web:1
    depends_on: [db]
    restart: always
    healthcheck: {test: ["CMD", "true"]}
  db:
    container_name: app_db
    image: postgres:16
    volumes: ["./data:/var/lib/postgresql/data"]
    restart: always
  worker:
    container_name: app_worker
    image: example/worker:1
    deploy: {replicas: 2, resources: {limits: {memory: 512M, cpus: "1"}}}
    healthcheck: {disable: true}
"""

JOURNEYS = """
journeys:
  home: {needs: [app_web, app_db], confidence: high}
extra_edges:
  - {from: app_web, to: app_worker, protocol: http, evidence: "calls worker at runtime"}
"""


@pytest.fixture
def topo(tmp_path: Path):
    c, j = tmp_path / "compose.yml", tmp_path / "journeys.yaml"
    c.write_text(COMPOSE)
    j.write_text(JOURNEYS)
    return build_topology(c, j)


def test_depends_on_becomes_declared_edge(topo):
    declared = [(e["from"], e["to"]) for e in topo["edges"] if e["kind"] == "declared"]
    assert declared == [("app_web", "app_db")]


def test_extra_edge_is_implicit_and_flagged_as_hidden_dependency(topo):
    implicit = [e for e in topo["edges"] if e["kind"] == "implicit"]
    assert [(e["from"], e["to"]) for e in implicit] == [("app_web", "app_worker")]
    titles = [a["title"] for a in topo["audit"]]
    assert any("Hidden dependency" in t and "app_worker" in t for t in titles)


def test_stateful_detection_and_limits(topo):
    by = {n["id"]: n for n in topo["nodes"]}
    assert by["app_db"]["kind"] == "stateful"
    assert by["app_web"]["kind"] == "stateless"
    assert by["app_worker"]["limits"] == {"memory": "512M", "cpus": "1"}
    assert by["app_worker"]["replicas"] == 2


def test_audit_groups_one_finding_per_issue_and_ranks_by_severity(topo):
    a = topo["audit"]
    limits = [f for f in a if "limits" in f["title"].lower()]
    assert len(limits) == 1
    assert "app_worker" not in limits[0]["services"]  # worker has limits
    assert {"app_web", "app_db"} <= set(limits[0]["services"])
    sev = [f["severity"] for f in a]
    assert sev == sorted(sev, key={"high": 0, "medium": 1, "low": 2}.get)
    assert [f["id"] for f in a] == [f"A-{i:02d}" for i in range(1, len(a) + 1)]


def test_disabled_healthcheck_is_not_counted_as_present(topo):
    by = {n["id"]: n for n in topo["nodes"]}
    assert by["app_web"]["has_healthcheck"] is True
    assert by["app_worker"]["has_healthcheck"] is False


def test_unknown_component_in_journeys_is_rejected(tmp_path):
    c, j = tmp_path / "c.yml", tmp_path / "j.yaml"
    c.write_text(COMPOSE)
    j.write_text("journeys:\n  x: {needs: [does_not_exist]}\n")
    with pytest.raises(ValueError, match="unknown components"):
        build_topology(c, j)


def test_not_a_compose_file_is_rejected(tmp_path):
    p = tmp_path / "x.yml"
    p.write_text("hello: world\n")
    with pytest.raises(ValueError):
        load_compose(p)


def test_journeys_yaml_matches_the_probe_registry():
    """journeys.yaml is hand-written; the probes are code. They must never silently disagree."""
    declared = yaml.safe_load(DEFAULT_JOURNEYS.read_text())["journeys"]
    assert set(declared) == set(JOURNEY_NEEDS)
    for name, spec in declared.items():
        assert set(spec["needs"]) == set(JOURNEY_NEEDS[name]), name


def test_audit_handles_empty_input():
    assert audit([], []) == []
