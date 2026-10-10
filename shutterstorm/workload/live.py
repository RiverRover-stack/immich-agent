"""Terminal live view of probe health. Pure rendering; reads from a ProbeRunner."""

from __future__ import annotations

import time

from rich.console import Group
from rich.table import Table
from rich.text import Text

from shutterstorm.workload.probes import ProbeRunner

FAILING_PCT = 50.0


def _status(summary: dict) -> Text:
    if summary["count"] == 0:
        return Text("… no data", style="dim")
    pct = summary["err_pct"]
    if pct >= FAILING_PCT:
        return Text("✖ FAILING", style="bold red")
    if pct > 0:
        return Text("▲ degraded", style="yellow")
    return Text("● healthy", style="green")


def render(runner: ProbeRunner) -> Group:
    elapsed = int(time.time() - runner.started_at) if runner.started_at else 0
    table = Table(
        title=f"ShutterStorm probes · {elapsed}s · window {int(runner.stats.window_s)}s",
        title_justify="left",
    )
    for col in ("JOURNEY", "STATUS", "REQ", "ERR%", "p95 ms", "LAST"):
        table.add_column(col, justify="left" if col in ("JOURNEY", "STATUS") else "right")

    for name in runner.journey_names:
        s = runner.stats.summary(name)
        p95 = f"{s['p95_ms']:.0f}" if s["p95_ms"] is not None else "—"
        last = str(s["last_status"]) if s["last_status"] is not None else "—"
        table.add_row(name, _status(s), str(s["count"]), f"{s['err_pct']:.0f}", p95, last)

    queues = Text("queues: ", style="bold")
    if runner.queues is None:
        queues.append("unreachable", style="red")
    else:
        parts = []
        for name, c in runner.queues.items():
            style = "red" if c["failed"] else ("yellow" if c["waiting"] or c["active"] else "dim")
            parts.append((f"{name} w={c['waiting']} a={c['active']} f={c['failed']}", style))
        for i, (txt, style) in enumerate(parts):
            queues.append(txt, style=style)
            if i < len(parts) - 1:
                queues.append("  ·  ", style="dim")
    if not runner.upload_enabled:
        return Group(table, queues)
    u = runner.upload_summary()
    uploads = Text("uploads: ", style="bold")
    gap = u["accepted"] - u["processed"] - u["stalled"]
    uploads.append(
        f"accepted {u['accepted']} · thumbnails {u['thumbnails']} · "
        f"indexed {u['indexed']} · processed {u['processed']}"
    )
    if gap > 0:
        uploads.append(f" · in flight {gap} (oldest {u['oldest_pending_s']:.0f}s)", style="yellow")
    if u["stalled"]:
        uploads.append(f" · STALLED {u['stalled']}", style="bold red")
    return Group(table, queues, uploads)
