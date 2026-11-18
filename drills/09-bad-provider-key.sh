TITLE="Invalid provider key -> 'Generate' step fails with the provider's error; nothing cached; request logged"
BREAKS="ns drills: OPENAI_API_KEY=sk-drill-invalid (one call to api.openai.com with a fake key, no cost)"
OBSERVE=("catalog: openai shows as configured (the gateway only knows a key is set, not that it is valid)"
         "manual chat to openai/gpt-5-mini: last=error with the provider's 401 message"
         "repeat: still a miss (errors are never cached); GET /api/requests shows status=error rows")
RESTORE="uninstall the drills release (the fake key only ever lived in ns drills)"
do_break() {
  drill_secrets
  k -n "$DRILL_NS" create secret generic drill-provider-keys --from-literal=OPENAI_API_KEY=sk-drill-invalid \
    --dry-run=client -o yaml | k apply -f - >/dev/null
  drills_release
  # envFrom is read at container start: restart so a release that was already up sees the new Secret
  k -n "$DRILL_NS" rollout restart deploy/gw-gateway >/dev/null
  drills_ready && ok "release up with a fake OpenAI key"
  local body; body=$(printf '{"message":"say hi","settings":{"provider":"openai","model":"gpt-5-mini","routing":"manual"}}')
  incluster "$DRILL_NS" "curl -s http://gw-gateway/api/catalog | grep -oE '\"name\":\"openai\"[^}]*\"configured\":(true|false)' | sed 's/^/    /'
$CHAT_SH chat first '$body'; chat repeat '$body'
curl -s 'http://gw-gateway/api/requests?limit=2' | grep -oE '\"error\":\"[^\"]{0,90}' | sed 's/^/    request log: /'"
}
do_fix() {
  drills_uninstall
  k -n "$DRILL_NS" delete secret drill-provider-keys --ignore-not-found >/dev/null
}
