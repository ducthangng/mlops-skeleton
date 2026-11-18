TITLE="Redis hangs -> gateway fails open (still 200, just no cache)"
BREAKS="CLIENT PAUSE 30000 ALL on router-dev's Redis: every command blocks for 30 s (like a hung node)"
OBSERVE=("requests during the pause: HTTP 200, x-cache: miss; ~+200 ms = 5 Redis calls x 50 ms timeout (no retries)"
         "metrics: gateway_cache_errors_total and gateway_rate_limit_errors_total increase"
         "rate limiter is fail-open too (RATE_LIMIT_FAIL_MODE=open); with failMode=closed these would be 503")
RESTORE="CLIENT UNPAUSE (or wait 30 s); deleting the Redis pod also works: Deployment recreates it, cache starts empty"
do_break() {
  local body; body=$(chat_json "why does my go program deadlock")
  incluster router-dev "curl -s -o /dev/null -w 'before: %{http_code} %{time_total}s\n' http://gw-router/v1/chat/completions -H 'content-type: application/json' -d '$body'"
  k -n router-dev exec deploy/gw-redis -- redis-cli CLIENT PAUSE 30000 ALL >/dev/null && ok "Redis paused for 30 s"
  incluster router-dev "for i in 1 2 3; do curl -s -o /dev/null -w 'paused: HTTP %{http_code} in %{time_total}s\n' http://gw-router/v1/chat/completions -H 'content-type: application/json' -d '$body'; done; curl -s http://gw-router/metrics | grep -E '^gateway_(cache_errors|rate_limit_errors)_total'"
}
do_fix() { k -n router-dev exec deploy/gw-redis -- redis-cli CLIENT UNPAUSE; }
