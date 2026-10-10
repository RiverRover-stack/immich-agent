"""ShutterStorm command line. Commands are added as modules land."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.live import Live

from shutterstorm import __version__
from shutterstorm.config import ConfigError, load_settings

app = typer.Typer(help="ShutterStorm: controlled chaos experiments against a local sandbox.")
console = Console()


@app.callback()
def main() -> None:
    """Keep ShutterStorm a command group (needed while it has few commands)."""


@app.command()
def version() -> None:
    """Print the ShutterStorm version."""
    typer.echo(__version__)


@app.command()
def probe(
    seconds: Annotated[
        int, typer.Option("--seconds", "-s", help="How long to run (0 = until Ctrl-C).")
    ] = 60,
    out: Annotated[
        Path | None, typer.Option("--out", help="Output folder (default runs/<timestamp>).")
    ] = None,
) -> None:
    """Run the journey probes and show live health. Writes probes.csv and queues.csv."""
    from shutterstorm.workload.live import render
    from shutterstorm.workload.probes import ProbeRunner

    try:
        settings = load_settings()
    except ConfigError as e:
        console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(1) from e
    if not settings.api_key:
        console.print("[red]error:[/red] no IMMICH_API_KEY in .env. Run: make bootstrap")
        raise typer.Exit(1)

    runner = ProbeRunner(settings, out_dir=out)

    async def drive() -> None:
        task = asyncio.create_task(runner.run(seconds or None))
        with Live(console=console, refresh_per_second=2) as live:
            while not task.done():
                live.update(render(runner))
                await asyncio.sleep(0.5)
            live.update(render(runner))
        await task  # surface any error from the run

    try:
        asyncio.run(drive())
    except KeyboardInterrupt:
        console.print("\n[yellow]stopped by user[/yellow]")
    except Exception as e:
        console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(1) from e
    console.print(f"\nResults: {runner.out_dir}/probes.csv · queues.csv")


def _executor():
    import docker

    from shutterstorm.chaos.executor import FaultExecutor
    from shutterstorm.chaos.policy import IMMICH_POLICY

    try:
        client = docker.from_env()
        client.ping()
    except docker.errors.DockerException as e:
        console.print(f"[red]error:[/red] cannot reach the Docker daemon ({e})")
        raise typer.Exit(1) from e
    return FaultExecutor(client, IMMICH_POLICY)


def _print_event(kind: str, message: str) -> None:
    style = {
        "applied": "bold red",
        "abort": "bold yellow",
        "error": "bold red",
        "verified": "green",
        "clamp": "yellow",
    }.get(kind, "dim")
    console.print(f"  [{style}]{kind:>8}[/{style}]  {message}")


@app.command()
def faults() -> None:
    """List the available faults and allowed targets."""
    from shutterstorm.chaos.faults import FAULTS
    from shutterstorm.chaos.policy import IMMICH_POLICY

    for f in FAULTS.values():
        console.print(f"[bold]{f.name:<13}[/bold] {f.description}")
    console.print("\n[bold]targets:[/bold] " + ", ".join(sorted(IMMICH_POLICY.allowed)))
    aliases = ", ".join(f"{a}={n}" for a, n in sorted(IMMICH_POLICY.aliases.items()))
    console.print(f"[bold]aliases:[/bold] {aliases}")


@app.command()
def inject(
    target: Annotated[str, typer.Argument(help="Container or alias, e.g. ml, db, redis, server.")],
    fault: Annotated[str, typer.Argument(help="stop | pause | cpu_throttle")],
    ttl: Annotated[float, typer.Option("--ttl", help="Seconds before auto-undo (max 120).")] = 30,
    cpus: Annotated[float, typer.Option("--cpus", help="For cpu_throttle (min 0.25).")] = 1.0,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation.")] = False,
) -> None:
    """Manually inject ONE fault. Same safety gate as the agent: allowlist, TTL, journal, undo."""
    from shutterstorm.chaos.faults import FaultError
    from shutterstorm.chaos.policy import PolicyError

    ex = _executor()
    console.print(
        f"[bold]inject[/bold] {fault} -> {target} for up to {ttl:g}s "
        "(stop early: [cyan]shutterstorm stop[/cyan] or Ctrl-C)"
    )
    if not yes and not typer.confirm("Proceed?"):
        raise typer.Exit(0)
    try:
        run = ex.run(target, fault, {"cpus": cpus}, ttl_s=ttl, on_event=_print_event)
    except (PolicyError, FaultError) as e:
        console.print(f"[red]refused:[/red] {e}")
        raise typer.Exit(1) from e
    console.print(
        f"\noutcome: [bold]{run.outcome}[/bold] ({run.reason}) · "
        f"fault held {run.undone_at - run.applied_at:.1f}s · "
        f"undone and verified: {'[green]yes[/green]' if run.undone_verified else '[red]NO[/red]'}"
    )
    if not run.undone_verified:
        raise typer.Exit(2)


@app.command()
def stop() -> None:
    """KILL SWITCH: abort any running fault (it is undone) and block new ones until `resume`."""
    from shutterstorm.chaos.journal import KillSwitch

    ks = KillSwitch()
    ks.trip()
    console.print(
        f"[bold yellow]kill switch tripped[/bold yellow] ({ks.path}). "
        "Running faults will undo. Clear with: shutterstorm resume"
    )


@app.command()
def resume() -> None:
    """Clear the kill switch so faults may run again."""
    from shutterstorm.chaos.journal import KillSwitch

    KillSwitch().clear()
    console.print("kill switch cleared")


@app.command()
def panic() -> None:
    """Undo EVERY fault still recorded in the journal (use after a crash)."""
    ex = _executor()
    pending = ex.journal.entries()
    if not pending:
        console.print("[green]journal is empty: nothing to undo[/green]")
        return
    results = ex.panic(on_event=_print_event)
    bad = [r for r in results if not r["restored"]]
    if bad:
        console.print(f"[red]{len(bad)} fault(s) could NOT be restored:[/red] {bad}")
        raise typer.Exit(2)
    console.print("[green]all faults restored[/green]")


def _run_dir(label: str) -> Path:
    from datetime import datetime

    from shutterstorm.config import ROOT

    return ROOT / "runs" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{label}"


def _settings_or_exit():
    try:
        settings = load_settings()
    except ConfigError as e:
        console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(1) from e
    if not settings.api_key:
        console.print("[red]error:[/red] no IMMICH_API_KEY in .env. Run: make bootstrap")
        raise typer.Exit(1)
    return settings


@app.command()
def drill() -> None:
    """PROVE the guard works: inject a mild fault with a tight threshold; it must auto-abort."""
    from shutterstorm.experiment import drill_spec, run_experiment

    settings = _settings_or_exit()
    spec = drill_spec()
    out = _run_dir("drill")
    console.print(
        "[bold]drill[/bold]: stop ML with a deliberately tight guard on 'search'. "
        f"The guard must abort long before the {spec.ttl_s:g}s TTL."
    )
    events = run_experiment(spec, settings, out, notify=_print_event)

    fault = events["fault"]
    held = fault["undone_at"] - fault["applied_at"]
    checks = {
        "guard tripped": events["guard"]["tripped"] is not None,
        "outcome is aborted_guard": fault["outcome"] == "aborted_guard",
        "undo verified": fault["undone_verified"],
        f"aborted early (held {held:.1f}s < ttl {spec.ttl_s:g}s)": held < spec.ttl_s,
        "journal empty": not _executor().journal.entries(),
    }
    console.print()
    for name, ok in checks.items():
        console.print(f"  {'[green]PASS[/green]' if ok else '[red]FAIL[/red]'}  {name}")
    console.print(f"\nguard reason: {events['guard']['tripped']}\nrun data: {out}")
    if not all(checks.values()):
        console.print("[bold red]DRILL FAILED: do not run real experiments.[/bold red]")
        raise typer.Exit(1)
    console.print("[bold green]DRILL PASSED: safety controls verified.[/bold green]")


@app.command()
def experiment(
    target: Annotated[str, typer.Argument(help="Container or alias.")],
    fault: Annotated[str, typer.Argument(help="stop | pause | cpu_throttle")],
    ttl: Annotated[float, typer.Option("--ttl", help="Seconds before auto-undo (max 120).")] = 30,
    cpus: Annotated[float, typer.Option("--cpus", help="For cpu_throttle.")] = 1.0,
    protected: Annotated[
        str, typer.Option("--protected", help="Comma-separated journeys the guard protects.")
    ] = "ping,auth_read,browse",
    warmup: Annotated[
        float, typer.Option("--warmup", help="Healthy seconds before the fault.")
    ] = 10,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    """Run ONE measured experiment: probes + guard + fault + recovery. Writes a run folder."""
    from shutterstorm.chaos.faults import FaultError
    from shutterstorm.chaos.policy import PolicyError
    from shutterstorm.experiment import ExperimentSpec, run_experiment

    settings = _settings_or_exit()
    spec = ExperimentSpec(
        id="manual",
        target=target,
        fault=fault,
        params={"cpus": cpus},
        ttl_s=ttl,
        protected=tuple(p.strip() for p in protected.split(",") if p.strip()),
        warmup_s=warmup,
    )
    out = _run_dir(f"{fault}-{target}")
    console.print(
        f"[bold]experiment[/bold] {fault} -> {target} · ttl {ttl:g}s · "
        f"guard protects: {', '.join(spec.protected)}"
    )
    if not yes and not typer.confirm("Proceed?"):
        raise typer.Exit(0)
    try:
        events = run_experiment(spec, settings, out, notify=_print_event)
    except (PolicyError, FaultError) as e:
        console.print(f"[red]refused:[/red] {e}")
        raise typer.Exit(1) from e
    f = events["fault"]
    console.print(
        f"\noutcome: [bold]{f['outcome']}[/bold] ({f['reason']}) · "
        f"undone+verified: {f['undone_verified']} · recovered: {events.get('recovered')}"
    )
    console.print(f"run data: {out}")


@app.command()
def discover(
    out: Annotated[Path, typer.Option("--out", help="Where to write the map.")] = Path("reports"),
) -> None:
    """Map the app: services, dependencies, journeys, and a static configuration audit."""
    import json

    import docker
    from rich.table import Table

    from shutterstorm.discover.render import render_graph
    from shutterstorm.discover.topology import build_topology

    try:
        client = docker.from_env()
        client.ping()
    except docker.errors.DockerException:
        client = None
        console.print("[yellow]docker unreachable: using compose file only[/yellow]")
    try:
        topo = build_topology(docker_client=client)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]error:[/red] {e}\nDid you run: make immich-fetch ?")
        raise typer.Exit(1) from e

    out.mkdir(parents=True, exist_ok=True)
    (out / "topology.json").write_text(json.dumps(topo, indent=2))
    render_graph(topo, out / "dependency-map.png")

    nodes = Table(title="Components")
    for col in ("container", "kind", "restart", "healthcheck", "limits", "depends_on"):
        nodes.add_column(col)
    for n in topo["nodes"]:
        limits = ", ".join(f"{k}={v}" for k, v in n["limits"].items() if v) or "none"
        nodes.add_row(
            n["id"],
            n["kind"],
            n["restart_policy"],
            "yes" if n["has_healthcheck"] else "no",
            limits,
            ", ".join(n["depends_on"]) or "-",
        )
    console.print(nodes)

    edges = Table(title="Dependencies")
    for col in ("from", "to", "kind", "evidence"):
        edges.add_column(col)
    for e in topo["edges"]:
        style = "bold red" if e["kind"] == "implicit" else None
        edges.add_row(e["from"], e["to"], e["kind"], e["evidence"][0][:70], style=style)
    console.print(edges)

    audit = Table(title="Static configuration audit")
    for col in ("id", "severity", "services", "finding"):
        audit.add_column(col)
    colors = {"high": "red", "medium": "yellow", "low": "dim"}
    for a in topo["audit"]:
        audit.add_row(
            a["id"],
            f"[{colors[a['severity']]}]{a['severity']}[/]",
            ", ".join(s.replace("immich_", "") for s in a["services"]),
            a["title"],
        )
    console.print(audit)
    console.print(f"\nwrote {out}/topology.json and {out}/dependency-map.png")


def _llm_client_or_none(required: bool = False):
    from shutterstorm.llm.client import LLMClient, LLMConfig, LLMNotConfigured

    try:
        return LLMClient(LLMConfig.from_env())
    except LLMNotConfigured as e:
        if required:
            console.print(f"[red]LLM not configured:[/red] {e}")
            raise typer.Exit(1) from e
        console.print(f"[yellow]no LLM ({e}): using deterministic fallback[/yellow]")
        return None


@app.command("llm-check")
def llm_check() -> None:
    """Check the configured LLM key: models, quota, JSON validity, tool calling, latency."""
    import time

    import httpx
    from pydantic import BaseModel

    from shutterstorm.llm.client import LLMError
    from shutterstorm.planner.llm import PlanResponse

    client = _llm_client_or_none(required=True)
    cfg = client.config
    console.print(f"provider [bold]{cfg.provider}[/bold] · {cfg.base_url}")
    ok_all = True

    class Ping(BaseModel):
        ok: bool

    try:
        r = httpx.get(
            f"{cfg.base_url}/models", headers={"Authorization": f"Bearer {cfg.api_key}"}, timeout=20
        )
        console.print(f"  auth /models: HTTP {r.status_code}")
        ok_all &= r.status_code == 200
    except httpx.HTTPError as e:
        console.print(f"  [red]unreachable: {type(e).__name__}[/red]")
        raise typer.Exit(1) from e

    for role in ("large", "small"):
        model = client.model_for(role)
        t0 = time.time()
        try:
            client.complete_json(
                role, "Answer with JSON.", 'Return {"ok": true}.', Ping, max_tokens=30
            )
            console.print(
                f"  [green]PASS[/green] {role:<5} {model}  json ok, {int((time.time() - t0) * 1000)} ms"
            )
        except LLMError as e:
            ok_all = False
            console.print(f"  [red]FAIL[/red] {role:<5} {model}  {e}")
    try:
        t0 = time.time()
        resp = client.complete_json(
            "large",
            "You plan chaos experiments. Fault is stop|pause|cpu_throttle.",
            "Containers: immich_machine_learning, immich_redis. Propose one experiment.",
            PlanResponse,
            max_tokens=700,
        )
        p = resp.proposals[0]
        console.print(
            f"  [green]PASS[/green] planner schema: {p.target}/{p.fault} "
            f"({int((time.time() - t0) * 1000)} ms)"
        )
    except LLMError as e:
        ok_all = False
        console.print(f"  [red]FAIL[/red] planner schema: {e}")
    if not ok_all:
        raise typer.Exit(1)


@app.command()
def run(
    max_experiments: Annotated[int, typer.Option("--max", help="Experiment budget.")] = 4,
    cooldown: Annotated[
        float, typer.Option("--cooldown", help="Seconds of calm between runs.")
    ] = 60,
    approve: Annotated[bool, typer.Option("--approve", help="Ask before each experiment.")] = False,
    planner: Annotated[str, typer.Option("--planner", help="auto | llm | fallback")] = "auto",
    faults_: Annotated[
        str, typer.Option("--faults", help="Comma-separated faults allowed this session.")
    ] = "stop,pause,cpu_throttle",
    baseline_path: Annotated[Path, typer.Option("--baseline")] = Path("runs/baseline.json"),
    report: Annotated[bool, typer.Option("--report/--no-report")] = True,
) -> None:
    """The closed loop: plan -> validate -> inject -> observe -> analyze -> re-plan -> report."""
    import json

    import docker

    from shutterstorm.agent import SessionConfig, run_session
    from shutterstorm.discover.topology import build_topology
    from shutterstorm.planner.fallback import FallbackPlanner
    from shutterstorm.planner.llm import LlmPlanner
    from shutterstorm.report.render import build_report_data, render

    settings = _settings_or_exit()
    if planner not in ("auto", "llm", "fallback"):
        console.print("[red]--planner must be auto, llm or fallback[/red]")
        raise typer.Exit(1)
    client = None if planner == "fallback" else _llm_client_or_none(required=planner == "llm")
    chosen = LlmPlanner(client) if client else FallbackPlanner()
    console.print(
        f"planner: [bold]{chosen.name}[/bold]"
        + (f" ({client.model_for('large')})" if client else "")
    )

    baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else None
    if baseline is None:
        console.print(
            "[yellow]no baseline found: guard latency rule disabled. "
            "Run: shutterstorm probe --seconds 300 first.[/yellow]"
        )
    out = _run_dir("session")
    topo = build_topology(docker_client=docker.from_env())

    def ask(v) -> bool:
        s = v.spec
        console.print(
            f"\n[bold]{s.id}[/bold] {s.fault} -> {s.target} ({s.ttl_s:g}s)\n  {s.hypothesis}"
        )
        return typer.confirm("Run it?")

    cfg = SessionConfig(
        settings=settings,
        topology=topo,
        planner=chosen,
        out_dir=out,
        baseline=baseline,
        max_experiments=max_experiments,
        cooldown_s=cooldown,
        approve=ask if approve else None,
        allowed_faults=tuple(f.strip() for f in faults_.split(",") if f.strip()),
    )
    res = run_session(cfg, notify=_print_event)
    console.print(f"\nsession finished: {res.stopped_because} · {len(res.results)} experiments")
    console.print(f"results: {out}/results.json")
    if report and res.results:
        writer = client  # same client: usage is accounted once
        data = build_report_data(
            res.results,
            topo,
            baseline,
            client=writer,
            session={"stopped_because": res.stopped_because},
            planner_history=getattr(chosen, "history", []),
        )
        png = Path("reports/dependency-map.png")
        md, html = render(data, out, png if png.exists() else None)
        console.print(f"report: {md}  ·  {html}")
        if client is not None:
            (out / "llm_usage.json").write_text(json.dumps(client.usage(), indent=2))
            u = client.usage()
            console.print(
                f"llm usage: {u['calls']} calls, {u['prompt_tokens']} prompt + "
                f"{u['completion_tokens']} completion tokens"
            )


@app.command()
def evaluate(
    baseline_s: Annotated[float, typer.Option("--baseline", help="Baseline seconds.")] = 60,
    ttl: Annotated[float, typer.Option("--ttl", help="Fault seconds.")] = 30,
    target: Annotated[str, typer.Option("--target")] = "ml",
    fault: Annotated[str, typer.Option("--fault")] = "stop",
    no_llm: Annotated[bool, typer.Option("--no-llm")] = False,
) -> None:
    """FINAL EVALUATION: baseline -> inject ML fault -> observe -> restore -> verify -> compare -> report."""
    from shutterstorm.evaluate import run_evaluation

    settings = _settings_or_exit()
    client = None if no_llm else _llm_client_or_none()
    out = _run_dir("evaluate")
    console.print(
        f"[bold]evaluate[/bold]: {fault} -> {target} · baseline {baseline_s:g}s · ttl {ttl:g}s"
    )
    res = run_evaluation(
        settings,
        out,
        baseline_s=baseline_s,
        target=target,
        fault=fault,
        ttl_s=ttl,
        llm=client,
        notify=_print_event,
    )
    console.print()
    for s in res.stages:
        console.print(
            f"  {'[green]PASS[/green]' if s.ok else '[red]FAIL[/red]'}  {s.name}  [dim]{s.detail}[/dim]"
        )
    if res.report_html:
        console.print(f"\nreport: {res.report_md}\n        {res.report_html}")
    if client is not None:
        import json as _json

        usage = client.usage()
        (out / "llm_usage.json").write_text(_json.dumps(usage, indent=2))
        console.print(
            f"llm usage: {usage['calls']} calls, {usage['prompt_tokens']} prompt + "
            f"{usage['completion_tokens']} completion tokens"
        )
    console.print(f"evidence: {out}")
    if not res.passed:
        console.print("[bold red]EVALUATION FAILED[/bold red]")
        raise typer.Exit(1)
    console.print("[bold green]EVALUATION PASSED[/bold green]")


@app.command()
def report(
    session_dir: Annotated[Path, typer.Argument(help="A runs/<...>-session folder.")],
    no_llm: Annotated[bool, typer.Option("--no-llm", help="Deterministic findings only.")] = False,
) -> None:
    """Rebuild the report from raw evidence (probes.csv etc.): re-analyzes, never trusts old numbers."""
    import json

    from shutterstorm.analyzer.metrics import analyze_experiment
    from shutterstorm.report.render import build_report_data, render

    baseline_path = Path("runs/baseline.json")
    baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else None
    topo_path = Path("reports/topology.json")
    if not topo_path.exists():
        console.print("[red]no reports/topology.json: run `shutterstorm discover` first[/red]")
        raise typer.Exit(1)
    topo = json.loads(topo_path.read_text())

    results = []
    for d in sorted(p for p in session_dir.iterdir() if (p / "events.json").exists()):
        ev = json.loads((d / "events.json").read_text())
        if not ev.get("fault"):
            continue
        r = analyze_experiment(d, baseline)
        r["run_dir"] = str(d)
        r["planner_reason"] = ev["spec"].get("reason", "")
        results.append(r)
    if not results:
        console.print(f"[red]no experiment folders with events.json under {session_dir}[/red]")
        raise typer.Exit(1)

    client = None if no_llm else _llm_client_or_none()
    data = build_report_data(results, topo, baseline, client=client)
    png = Path("reports/dependency-map.png")
    md, html = render(data, session_dir, png if png.exists() else None)
    console.print(f"{len(results)} experiments re-analyzed from raw evidence")
    console.print(f"report: {md}  ·  {html}")
    if client is not None:
        u = client.usage()
        (session_dir / "llm_usage.json").write_text(json.dumps(u, indent=2))
        console.print(
            f"llm usage: {u['calls']} calls, {u['prompt_tokens']} prompt + "
            f"{u['completion_tokens']} completion tokens"
        )


@app.command()
def cleanup(
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only count.")] = False,
) -> None:
    """Delete the photos the probes uploaded (ss-probe-*). Your seed photos are never touched."""
    from shutterstorm.workload.cleanup import cleanup as do_cleanup

    res = do_cleanup(_settings_or_exit(), dry_run=dry_run)
    console.print(f"found {res['found']} probe uploads; deleted {res['deleted']}")


if __name__ == "__main__":
    app()
