TITLE="Flaky test -> same commit passes and fails"
BREAKS="on product branch drill/12-flaky-test, a test fails 50% of the time"
OBSERVE=("local: 6 runs of the same commit, mixed results (printed above)"
         "Jenkins: Replay / rebuild the same commit -> different results; logtriage says 'test' both times"
         "fix in real life: make it deterministic (seed, fake clock); quarantine with a marker only as a stopgap")
RESTORE="delete the drill branch"
do_break() {
  local wt; wt=$(drill_branch 12-flaky-test)
  printf 'import random\n\n\ndef test_flaky_drill():\n    assert random.random() < 0.5   # drill 12\n' > "$wt/gateway/quick_tests/test_flaky_drill.py"
  git -C "$wt" add gateway/quick_tests/test_flaky_drill.py && drill_commit "$wt" "flaky test"
  for i in 1 2 3 4 5 6; do
    (cd "$wt/gateway" && uv run --quiet pytest -q -p no:cacheprovider quick_tests/test_flaky_drill.py >/dev/null 2>&1) \
      && echo "    run $i: pass" || echo "    run $i: FAIL"
  done
  push_hint 12-flaky-test
}
do_fix() { drill_branch_remove 12-flaky-test; }
