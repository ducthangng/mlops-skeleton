TITLE="Semantic cache tau too low -> wrong answer served (false hit)"
BREAKS="ns drills: SEMANTIC_CACHE_THRESHOLD=0.85 (default 0.95)"
OBSERVE=("'derivative of x^3' is answered with the cached answer for 'derivative of x^2' (x-cache: semantic)"
         "qualify cache stage with the same tau: false-hit rate > 0, each false hit listed with its cosine"
         "why 0.95: lowest tau with 0 false hits on evals/semantic_pairs.jsonl (docs/results/semantic_tau_hashing.md)")
RESTORE="uninstall the drills release (Git value 0.95 never changed)"
do_break() {
  drills_release --set gateway.semanticCache.threshold=0.85 && drills_ready
  local a b; a=$(chat_json "what is the derivative of x^2"); b=$(chat_json "what is the derivative of x^3")
  incluster "$DRILL_NS" "curl -s http://gw-router/v1/chat/completions -H 'content-type: application/json' -d '$a' >/dev/null
curl -s -D /tmp/h http://gw-router/v1/chat/completions -H 'content-type: application/json' -d '$b' | sed -E 's/.*\"content\":\"([^\"]*)\".*/answer: \1/'; echo
grep -iE '^x-(cache|cache-similarity)' /tmp/h"
  (cd "$ROOT" && uv run --quiet python -m qualify.run --stage cache --env SEMANTIC_CACHE_THRESHOLD=0.85 \
    --out "$WORKTREES/qualify-tau" 2>/dev/null | head -n 4) || true
}
do_fix() { h -n "$DRILL_NS" uninstall gw >/dev/null 2>&1 && ok "drills release removed" || skip "nothing installed"; }
