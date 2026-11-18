TITLE="Failing unit test -> build red, nothing reaches the cluster"
BREAKS="on product branch drill/01-failing-test, test_joke_goes_to_chitchat_route expects the joke to route to code"
OBSERVE=("local: pytest fails (shown above)"
         "Jenkins: ai-gateway/drill%2F01-failing-test fails in 'Quick tests'; Build image + Bump never run"
         "logtriage: category test, evidence = the FAILED quick_tests/... line"
         "ArgoCD/gateway-dev unchanged: CI produced no image and no GitOps commit")
RESTORE="delete the drill branch (local + remote)"
do_break() {
  local wt; wt=$(drill_branch 01-failing-test)
  sed -i.bak 's/tell me a joke").route == "chitchat"/tell me a joke").route == "code"  # drill 01/' \
    "$wt/gateway/quick_tests/test_router.py" && rm "$wt/gateway/quick_tests/test_router.py.bak"
  drill_commit "$wt" "failing unit test"
  (cd "$wt/gateway" && uv run --quiet pytest -q quick_tests/test_router.py 2>&1 | tail -n 3) || true
  push_hint 01-failing-test
}
do_fix() { drill_branch_remove 01-failing-test; }
