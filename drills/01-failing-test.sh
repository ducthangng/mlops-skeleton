TITLE="Failing unit test -> build red, nothing reaches the cluster"
BREAKS="on branch drill/01-failing-test, test_tie_goes_to_first_route expects the wrong route"
OBSERVE=("local: pytest fails (shown above)"
         "Jenkins: router/drill%2F01-failing-test fails in 'Unit + API tests'; Build image + Bump never run"
         "logtriage: category test, evidence = the FAILED tests/... line"
         "ArgoCD/router-dev unchanged: CI produced no image and no GitOps commit")
RESTORE="delete the drill branch (local + remote)"
do_break() {
  local wt; wt=$(drill_branch 01-failing-test)
  sed -i.bak 's/    assert d.route == "code"$/    assert d.route == "math"   # drill 01/' "$wt/tests/unit/test_classifier.py" && rm "$wt/tests/unit/test_classifier.py.bak"
  drill_commit "$wt" "failing unit test"
  (cd "$wt" && uv run --quiet pytest -q tests/unit/test_classifier.py 2>&1 | tail -n 3) || true
  push_hint 01-failing-test
}
do_fix() { drill_branch_remove 01-failing-test; }
