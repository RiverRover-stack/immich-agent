"""Render the dependency map as an image (matplotlib; no display server needed)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx

SHORT = {
    "immich_server": "server\n(API + jobs)",
    "immich_machine_learning": "machine-learning\n(search, faces, OCR)",
    "immich_postgres": "postgres\n(metadata + vectors)",
    "immich_redis": "redis/valkey\n(job queue)",
}
POSITIONS = {
    "immich_server": (0.0, 0.0),
    "immich_machine_learning": (-1.6, -1.4),
    "immich_postgres": (1.6, -1.4),
    "immich_redis": (0.0, -1.9),
}


def render_graph(topology: dict[str, Any], path: Path) -> Path:
    g = nx.DiGraph()
    for n in topology["nodes"]:
        g.add_node(n["id"], kind=n["kind"])
    declared = [(e["from"], e["to"]) for e in topology["edges"] if e["kind"] == "declared"]
    implicit = [(e["from"], e["to"]) for e in topology["edges"] if e["kind"] == "implicit"]
    g.add_edges_from(declared + implicit)

    pos = {n: POSITIONS.get(n) or nx.spring_layout(g, seed=3)[n] for n in g.nodes}
    fig, ax = plt.subplots(figsize=(9, 6.2))
    colors = ["#e6a23c" if g.nodes[n]["kind"] == "stateful" else "#6aa5ff" for n in g.nodes]
    nx.draw_networkx_nodes(g, pos, node_color=colors, node_size=8200, edgecolors="#333", ax=ax)
    nx.draw_networkx_labels(
        g, pos, labels={n: SHORT.get(n, n) for n in g.nodes}, font_size=7.5, ax=ax
    )
    nx.draw_networkx_edges(
        g,
        pos,
        edgelist=declared,
        arrows=True,
        arrowsize=22,
        node_size=8200,
        edge_color="#333",
        width=2,
        ax=ax,
    )
    nx.draw_networkx_edges(
        g,
        pos,
        edgelist=implicit,
        arrows=True,
        arrowsize=22,
        node_size=8200,
        edge_color="#d6336c",
        width=2.4,
        style="dashed",
        ax=ax,
    )
    ax.set_title(
        "Immich v3.3.1 dependency map\n"
        "solid = declared in compose · dashed red = HIDDEN (not declared) · "
        "orange = stateful",
        fontsize=10,
    )
    ax.set_axis_off()
    ax.margins(0.25)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path
