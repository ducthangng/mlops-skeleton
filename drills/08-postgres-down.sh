TITLE="Postgres down -> chats fail, but /api/healthz stays green (readiness blind spot)"
BREAKS="ns drills: healthy release, then scale statefulset gw-postgres to 0"
OBSERVE=("before: chat ok"
         "Postgres down: chat fails (HTTP 500: the user turn is stored before streaming starts)"
         "/api/healthz still 200 and the pod stays Ready: the probe does not check the database"
         "lesson: a liveness probe must not depend on the DB (restarts would not fix it), but readiness could")
RESTORE="uninstall the drills release (Git-managed envs were never touched)"
do_break() {
  drills_release && drills_ready && ok "healthy release up"
  local body; body=$(chat_json "hello there")
  incluster "$DRILL_NS" "$CHAT_SH chat before '$body'"
  k -n "$DRILL_NS" scale statefulset gw-postgres --replicas=0 >/dev/null
  k -n "$DRILL_NS" wait --for=delete pod/gw-postgres-0 --timeout=60s >/dev/null 2>&1 || true
  ok "postgres scaled to 0"
  incluster "$DRILL_NS" "$CHAT_SH chat db-down '$body'; curl -s -o /dev/null -w '    healthz:   HTTP %{http_code}\n' http://gw-gateway/api/healthz"
  k -n "$DRILL_NS" get pods -l app=gw-gateway
}
do_fix() { drills_uninstall; }
