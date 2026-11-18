TITLE="Redis hangs -> gateway fails open (chat still answers, just no cache)"
BREAKS="CLIENT PAUSE 30000 ALL on gateway-dev's Redis: every command blocks for 30 s (like a hung node)"
OBSERVE=("before: a repeated prompt is a cache hit"
         "during the pause: last=done, cache_status=error, the model answers; +~50 ms (one GATEWAY_CACHE_TIMEOUT_S, no retries)"
         "UI: the 'Check cache' step is a warning ('Unavailable, continuing without cache'), not an error")
RESTORE="CLIENT UNPAUSE (or wait 30 s); deleting the Redis pod also works: the Deployment recreates it, cache starts empty"
do_break() {
  local body; body=$(chat_json "why does my go program deadlock")
  incluster gateway-dev "$CHAT_SH chat warm '$body' >/dev/null; chat before '$body'"
  k -n gateway-dev exec deploy/gw-redis -- redis-cli CLIENT PAUSE 30000 ALL >/dev/null && ok "Redis paused for 30 s"
  incluster gateway-dev "$CHAT_SH for i in 1 2 3; do chat paused-\$i '$body'; done"
}
do_fix() { k -n gateway-dev exec deploy/gw-redis -- redis-cli CLIENT UNPAUSE; }
