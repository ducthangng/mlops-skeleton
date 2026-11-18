TITLE="Failing unit test -> build red, nothing reaches the cluster"
BREAKS="on product branch drill/01-failing-test, test_real_routes_yaml_smoke expects 'solve x^2 - 4 = 0' to route to code"
OBSERVE=("local: pytest fails (shown above)"
         "Jenkins: ai-gateway/drill%2F01-failing-test fails in 'Unit + API tests'; Build image + Bump never run"
         "logtriage: category test, evidence = the FAILED tests/... line"
         "ArgoCD/gateway-dev unchanged: CI produced no image and no GitOps commit")
RESTORE="delete the drill branch (local + remote)"
do_break() {
  local wt; wt=$(drill_branch 01-failing-test)
  sed -i.bak 's/route(r, "solve x^2 - 4 = 0").reason.startswith("route math")/route(r, "solve x^2 - 4 = 0").reason.startswith("route code")  # drill 01/' \
    "$wt/gateway/tests/test_routing.py" && rm "$wt/gateway/tests/test_routing.py.bak"
  drill_commit "$wt" "failing unit test"
  (cd "$wt/gateway" && uv run --quiet pytest -q tests/test_routing.py 2>&1 | tail -n 3) || true
  push_hint 01-failing-test
}
do_fix() { drill_branch_remove 01-failing-test; }
