TITLE="Router memory limit too low -> OOMKilled, CrashLoopBackOff; old pod keeps serving"
BREAKS="ns drills: healthy release, then upgrade with resources.limits.memory=40Mi"
OBSERVE=("new pod: OOMKilled -> CrashLoopBackOff (kubectl describe: Last State Terminated, Reason OOMKilled, Exit Code 137)"
         "old pod still Running and serving: rolling update waits for readiness (maxUnavailable rounds to 0 with 1 replica)")
RESTORE="uninstall the drills release"
do_break() {
  drills_release && drills_ready && ok "healthy release up"
  drills_release --set resources.limits.memory=40Mi --set resources.requests.memory=32Mi
  for _ in $(seq 1 24); do
    k -n "$DRILL_NS" get pods -l app=gw-router -o jsonpath='{.items[*].status.containerStatuses[*].lastState.terminated.reason}' | grep -q OOMKilled && break
    sleep 5
  done
  k -n "$DRILL_NS" get pods -l app=gw-router
  k -n "$DRILL_NS" get pods -l app=gw-router -o jsonpath='{range .items[*]}{.metadata.name}: {.status.containerStatuses[0].lastState.terminated.reason} {.status.containerStatuses[0].lastState.terminated.exitCode}{"\n"}{end}'
  incluster "$DRILL_NS" 'curl -s -o /dev/null -w "Service: HTTP %{http_code}\n" http://gw-router/healthz'
}
do_fix() { h -n "$DRILL_NS" uninstall gw >/dev/null 2>&1 && ok "drills release removed" || skip "nothing installed"; }
