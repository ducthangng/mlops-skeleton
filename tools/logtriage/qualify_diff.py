"""Diff a qualification run against golden: report ONLY new failures and regressed metrics.

Unchanged stages are summarised in one line; stages that did not run (fail fast) are listed.
Each new failure carries its evidence line and its location in <stage>.log.
"""

import json
from pathlib import Path

from qualify.compare import compare

ORDER = ["config_lint", "contract", "routing", "cache", "load"]


def diff_run(run_dir: Path, golden_dir: Path) -> tuple[dict, str]:
    stages, regressions, unchanged, not_run = {}, [], [], []
    for stage in ORDER:
        g, r = golden_dir / f"{stage}.json", run_dir / f"{stage}.json"
        if not g.exists():
            continue
        if not r.exists():
            not_run.append(stage)
            continue
        run = json.loads(r.read_text())
        d = compare(run, json.loads(g.read_text()))
        stages[stage] = {"skipped": run.get("skipped"), **d}
        for f in d["new_failures"]:
            regressions.append({"stage": stage, "kind": "new_failure", "message": f["message"],
                                "evidence": f["evidence"], "where": f"{stage}.log:{f['line']}"})
        for m in d["regressed_metrics"]:
            regressions.append({"stage": stage, "kind": "regressed_metric", "metric": m["metric"],
                                "golden": m["golden"], "run": m["run"], "limit": m["limit"],
                                "evidence": m.get("evidence")})
        if not d["new_failures"] and not d["regressed_metrics"]:
            unchanged.append(f"{stage} (cached)" if run.get("skipped") else stage)

    md = ["# Qualification vs golden", ""]
    if not regressions:
        md.append("No new failures and no regressed metrics.")
    else:
        md += [f"**{len(regressions)} regression(s)** (only what is new vs golden):", ""]
        for x in regressions:
            if x["kind"] == "new_failure":
                md.append(f"- `{x['stage']}` new failure ({x['where']}): `{x['evidence']}`")
            else:
                md.append(f"- `{x['stage']}` {x['metric']}: {x['run']} vs golden {x['golden']} "
                          f"(limit {x['limit']}). Evidence: `{x['evidence']}`")
    md += ["", f"Unchanged: {', '.join(unchanged) or '-'}",
           f"Not run (fail fast): {', '.join(not_run) or '-'}"]
    report = {"regressions": regressions, "unchanged": unchanged, "not_run": not_run, "stages": stages}
    return report, "\n".join(md)
