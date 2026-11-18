TITLE="Truncated answer (max_tokens hit) is never cached; the same prompt with room is"
BREAKS="nothing breaks: two chats with max_tokens=5 (answer cut off), then two with the default"
OBSERVE=("max_tokens=5: stop_reason=max_tokens, cache_status=miss both times (a cut-off answer must not be replayed)"
         "default max_tokens: miss, then hit"
         "same rule for errors and refusals: only complete answers are stored (gateway/service.py step 5)")
RESTORE="nothing to restore"
do_break() {
  local ns=${NS:-gateway-dev} tag; tag=$RANDOM
  local cut full
  cut=$(chat_json "drill 15 run $tag please explain caching" '"routing":"manual","max_tokens":5')
  full=$(chat_json "drill 15 run $tag please explain caching" '"routing":"manual"')
  incluster "$ns" "$CHAT_SH chat cut-1 '$cut'; chat cut-2 '$cut'; chat full-1 '$full'; chat full-2 '$full'"
}
do_fix() { echo "    nothing to do"; }
