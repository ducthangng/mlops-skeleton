TITLE="Release qualification: SCENARIO=${SCENARIO:-bad} (clean | bad | partial)"
BREAKS="bad = broken routes.yaml (+ unknown model); partial = valid config with routing regressions; clean = as-is"
OBSERVE=("bad: stops at config_lint in < 1 s, later stages listed as 'not run (fail fast)'"
         "partial: config_lint passes, contract reused from cache, routing fails with one evidence line per regressed prompt"
         "clean twice: second run skips every stage (inputs unchanged)"
         "Jenkins: JENKINS=1 SCENARIO=partial make drill-16 runs the 'qualify' job instead")
RESTORE="nothing to restore: scenarios are overlays, the product repo is not modified"
do_break() {
  local sc=${SCENARIO:-bad}
  if [[ ${JENKINS:-0} == 1 ]]; then
    local n r; n=$(jenkins_build /job/qualify "SCENARIO=$sc"); r=$(jenkins_wait /job/qualify "$n")
    echo "    qualify #$n ($sc): $r"
    jenkins_api GET "/job/qualify/$n/artifact/gateway/reports/qualify/qualify-diff.md" </dev/null | tail -n +2; echo
    return
  fi
  (cd "$APP_DIR/gateway" && GATEWAY_REDIS_URL=${GATEWAY_REDIS_URL:-redis://localhost:6390/0} \
     uv run --quiet python -m qualify.run --all --scenario "$sc" --out "$WORKTREES/qualify-$sc" \
     --cache-dir "$WORKTREES/qualify-cache" 2>/dev/null | grep -E '^\[|^   '; \
   uv run --quiet python -m qualify.diff --run "$WORKTREES/qualify-$sc" --no-fail) || true
}
do_fix() { echo "    nothing to do"; }
