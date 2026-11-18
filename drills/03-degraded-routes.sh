TITLE="Degraded routes.yaml -> routing eval gate blocks the release"
BREAKS="on product branch drill/03-degraded-routes, the math route keeps 5 keywords and no patterns (config still valid, tests pass)"
OBSERVE=("local: eval gate FAIL (accuracy below baseline - 2 points), shown above"
         "Jenkins: everything green until 'Routing eval gate'; gateway/reports/eval.json archived with the wrong prompts"
         "this is why the gate exists: unit tests and lint cannot see a quality regression")
RESTORE="delete the drill branch"
do_break() {
  local wt; wt=$(drill_branch 03-degraded-routes)
  (cd "$wt/gateway" && uv run --quiet python - routes.yaml <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1]))
for r in cfg["routes"]:
    if r["name"] == "math":
        r["keywords"], r["patterns"] = r["keywords"][:5], []
open(sys.argv[1], "w").write(yaml.safe_dump(cfg, sort_keys=False))
PY
  )
  drill_commit "$wt" "degraded routes.yaml"
  (cd "$wt/gateway" && uv run --quiet python -m evals.run --split test --gate evals/baseline.json 2>&1 | tail -n 2) || true
  push_hint 03-degraded-routes
}
do_fix() { drill_branch_remove 03-degraded-routes; }
