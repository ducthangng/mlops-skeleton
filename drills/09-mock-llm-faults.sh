TITLE="LLM backend returns 500 / is slow -> retries, fallback model, then 502"
BREAKS="ns drills: (a) mock-llm fails every call for model 'coder'; (b) mock-llm takes 3 s while the gateway timeout is 1 s"
OBSERVE=("(a) code prompt answered by x-model: small-chat (fallback) after 3 attempts on coder"
         "(b) every attempt times out -> HTTP 502 after ~ (1 s x 3) x 2 models; metric reason=\"timeout\""
         "gateway_upstream_errors_total{model,reason} and gateway_upstream_fallbacks_total in /metrics")
RESTORE="uninstall the drills release (Git-managed envs were never touched)"
do_break() {
  local body; body=$(chat_json "python TypeError in my script")
  drills_release --set mockLlm.errorRate=1 --set mockLlm.errorModels=coder && drills_ready
  incluster "$DRILL_NS" "curl -s -D - -o /dev/null http://gw-router/v1/chat/completions -H 'content-type: application/json' -d '$body' | grep -iE '^(HTTP|x-model|x-route)'"
  drills_release --set mockLlm.latencyMs=3000 --set gateway.upstream.timeoutS=1 && drills_ready
  incluster "$DRILL_NS" "curl -s -o /dev/null -w 'slow backend: HTTP %{http_code} after %{time_total}s\n' http://gw-router/v1/chat/completions -H 'content-type: application/json' -d '$body'; curl -s http://gw-router/metrics | grep -E '^gateway_upstream_(errors|fallbacks)_total'"
}
do_fix() { h -n "$DRILL_NS" uninstall gw >/dev/null 2>&1 && ok "drills release removed" || skip "nothing installed"; }
