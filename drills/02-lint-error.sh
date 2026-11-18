TITLE="Lint error -> parallel Lint stage fails"
BREAKS="on product branch drill/02-lint-error, gateway/plugins/redis_cache.py gets an unused import"
OBSERVE=("local: ruff F401 (shown above)"
         "Jenkins: 'Lint' fails while 'Unit + API tests' (parallel) still finishes -> both results in one build"
         "logtriage: category lint, evidence = gateway/plugins/redis_cache.py:1:1: F401 ...")
RESTORE="delete the drill branch"
do_break() {
  local wt f; wt=$(drill_branch 02-lint-error); f=$wt/gateway/gateway/plugins/redis_cache.py
  printf 'import sys  # drill 02\n%s\n' "$(cat "$f")" > "$f"
  drill_commit "$wt" "lint error"
  (cd "$wt/gateway" && uv run --quiet ruff check gateway --output-format=concise 2>&1 | head -n 3) || true
  push_hint 02-lint-error
}
do_fix() { drill_branch_remove 02-lint-error; }
