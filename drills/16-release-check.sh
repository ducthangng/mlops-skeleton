TITLE="Release check catches a routing change the quick tests miss"
BREAKS="on product branch drill/16-release-check, routes.yaml loses the keyword 'joke' (route chitchat)"
OBSERVE=("local: quick tests still pass, release check prints FAIL routes_unchanged with the 2 joke prompts (shown above)"
         "Jenkins: the 'qualify' job builds main; to see it there, make the same edit on main and push (demo in custom-ai-gateway/gateway/README.md)")
RESTORE="delete the drill branch (local + remote)"
do_break() {
  local wt; wt=$(drill_branch 16-release-check)
  sed -i.bak 's/ joke,//' "$wt/gateway/routes.yaml" && rm "$wt/gateway/routes.yaml.bak"
  drill_commit "$wt" "drop keyword joke"
  (cd "$wt/gateway" && echo "    quick tests: $(uv run --quiet pytest -q 2>&1 | tail -n 1)" && \
     uv run --quiet python -m release_check.run 2>&1 | sed 's/^/    /') || true
}
do_fix() { drill_branch_remove 16-release-check; }
