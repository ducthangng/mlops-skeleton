"""Compare one stage summary with its golden summary. Shared by qualify.run and logtriage."""


def compare(run: dict, golden: dict) -> dict:
    """Return ONLY what is new: failures whose id is not in golden, metrics outside tolerance.

    golden["compare"][metric] = {"better": "lower"|"higher", "rel": 0.5, "abs": 20}
      lower is better  -> regression if run > golden * (1 + rel) + abs
      higher is better -> regression if run < golden * (1 - rel) - abs
    """
    known = {f["id"] for f in golden.get("failures", [])}
    new_failures = [f for f in run.get("failures", []) if f["id"] not in known]
    regressed = []
    for name, rule in golden.get("compare", {}).items():
        g, r = golden["metrics"].get(name), run.get("metrics", {}).get(name)
        if g is None or r is None:
            continue
        rel, abs_ = rule.get("rel", 0.0), rule.get("abs", 0.0)
        if rule["better"] == "lower":
            limit = g * (1 + rel) + abs_
            bad = r > limit + 1e-9
        else:
            limit = g * (1 - rel) - abs_
            bad = r < limit - 1e-9
        if bad:
            regressed.append({"metric": name, "golden": g, "run": r, "limit": round(limit, 4),
                              "evidence": run.get("evidence", {}).get(name)})
    return {"new_failures": new_failures, "regressed_metrics": regressed}
