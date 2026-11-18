TITLE="Failing health probe -> new pods never Ready, liveness restarts them, rollout stalls"
BREAKS="ns drills: healthy release, then upgrade with probePath=/api/nope (404)"
OBSERVE=("new pod 0/1 Running, never added to the Service (readiness); first restart only after ~60 s:"
         "the startupProbe (30 x 2 s) must fail before the kubelet restarts the container"
         "old pod keeps serving; kubectl rollout status hangs until progressDeadlineSeconds")
RESTORE="uninstall the drills release"
do_break() {
  drills_release && drills_ready && ok "healthy release up"
  drills_release --set probePath=/api/nope
  sleep 80
  k -n "$DRILL_NS" get pods -l app=gw-gateway
  incluster "$DRILL_NS" 'curl -s -o /dev/null -w "    Service: HTTP %{http_code}\n" http://gw-gateway/api/healthz'
}
do_fix() { drills_uninstall; }
