"""logtriage CLI.

  # local log files
  uv run python -m tools.logtriage analyze tools/logtriage/samples/*.log

  # Jenkins (make jenkins: port-forward on :8080)
  JENKINS_PASSWORD=$(make -s passwords | ...) \
  uv run python -m tools.logtriage jenkins --url http://localhost:8080 --job ai-gateway --out reports/triage

Qualification diffs (new failures vs golden) live with the product: python -m qualify.diff in custom-ai-gateway/gateway.
"""

import json
from pathlib import Path
from typing import Annotated

import httpx
import typer

from tools.logtriage.classify import classify
from tools.logtriage.jenkins import Jenkins
from tools.logtriage.stats import slow_builds

app = typer.Typer(add_completion=False, help="Classify CI failures and flag slow builds (Jenkins logs or local files).")


def _write(out: Path | None, name: str, data: dict, markdown: str) -> None:
    typer.echo(markdown)
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{name}.json").write_text(json.dumps(data, indent=2))
        (out / f"{name}.md").write_text(markdown)


def _md_row(*cells) -> str:
    return "| " + " | ".join(str(c).replace("|", "\\|") for c in cells) + " |"


@app.command()
def analyze(logs: list[Path], out: Annotated[Path | None, typer.Option(help="dir for triage.json/.md")] = None):
    """Classify local console logs."""
    rows, lines = [], ["# CI failure triage", "", _md_row("log", "category", "line", "evidence"),
                       _md_row("---", "---", "---:", "---")]
    for p in logs:
        t = classify(p.read_text(errors="replace"))
        rows.append({"log": str(p), **t.as_dict()})
        ev = t.evidence
        lines.append(_md_row(p.name, t.category, ev.line_no if ev else "", f"`{ev.line}`" if ev else ""))
    _write(out, "triage", {"builds": rows}, "\n".join(lines))


@app.command()
def jenkins(
    url: Annotated[str, typer.Option(envvar="JENKINS_URL")] = "http://localhost:8081",
    job: Annotated[str, typer.Option(help="job (folder path), e.g. ai-gateway or qualify")] = "ai-gateway",
    branch: Annotated[str, typer.Option(help="multibranch branch; '' for a plain pipeline job")] = "main",
    last: int = 20,
    user: Annotated[str, typer.Option(envvar="JENKINS_USER")] = "admin",
    password: Annotated[str, typer.Option(envvar="JENKINS_PASSWORD", help="password or API token")] = "",
    sigma: Annotated[float, typer.Option(help="slow if > mean + sigma * stdev of the other builds")] = 3.0,
    out: Annotated[Path | None, typer.Option()] = None,
):
    """Fetch the last N builds of a job, classify failures, flag slow builds."""
    branch = branch or None
    j = Jenkins(url, user, password or None)
    try:
        builds = j.builds(job, branch, last)
    except httpx.HTTPStatusError as e:
        hint = " (set JENKINS_PASSWORD)" if e.response.status_code in (401, 403) else ""
        typer.echo(f"Jenkins answered HTTP {e.response.status_code} for {e.request.url.path}{hint}", err=True)
        raise typer.Exit(2) from e
    finished = {b.number: b.duration_s for b in builds if b.result is not None}
    slow = {s.number: s for s in slow_builds(finished, k=sigma)}
    rows = []
    title = f"{job}/{branch}" if branch else job
    lines = [f"# Triage: {title} (last {len(builds)} builds)", "",
             _md_row("build", "result", "duration", "category", "evidence"),
             _md_row("---:", "---", "---:", "---", "---")]
    for b in builds:
        row = {"number": b.number, "result": b.result, "duration_s": round(b.duration_s, 1),
               "slow": b.number in slow}
        cat, ev_txt = "", ""
        if b.result in ("FAILURE", "ABORTED", "UNSTABLE"):
            t = classify(j.console(job, b.number, branch))
            row["triage"] = t.as_dict()
            cat = t.category
            ev_txt = f"L{t.evidence.line_no}: `{t.evidence.line}`" if t.evidence else ""
        dur = f"{b.duration_s:.0f}s" + (" **SLOW**" if b.number in slow else "")
        lines.append(_md_row(b.number, b.result or "running", dur, cat, ev_txt))
        rows.append(row)
    if slow:
        lines += ["", f"Slow builds (> mean + {sigma:g} sigma of the other finished builds):"]
        lines += [f"- #{s.number}: {s.duration_s:.0f}s > {s.threshold_s:.0f}s "
                  f"(mean {s.mean_s:.0f}s, sigma {s.stdev_s:.0f}s)" for s in slow.values()]
    _write(out, "triage", {"job": title, "builds": rows, "slow": [vars(s) for s in slow.values()]},
           "\n".join(lines))
