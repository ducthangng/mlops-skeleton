TITLE="Delete the pod behind a port-forward -> tunnel dies, Service keeps working"
BREAKS="port-forward to one gateway-prod pod (2 replicas), then delete that pod"
OBSERVE=("curl via localhost:18090 works, then fails after the delete (the tunnel was bound to that pod)"
         "in-cluster curl through the Service still returns 200: the other replica serves (lesson 1)")
RESTORE="nothing: the Deployment already replaced the pod; start a new port-forward if you need one"
do_break() {
  local pod pf
  pod=$(k -n gateway-prod get pods -l app=gw-gateway -o jsonpath='{.items[0].metadata.name}') \
    || die "no gw-gateway pod in gateway-prod (promote a tag first: make promote)"
  k -n gateway-prod port-forward "pod/$pod" 18090:8090 >/dev/null 2>&1 & pf=$!
  sleep 3
  curl -s -o /dev/null -w "    via port-forward to $pod: HTTP %{http_code}\n" localhost:18090/api/healthz || true
  k -n gateway-prod delete pod "$pod" --wait=false >/dev/null && ok "deleted $pod"
  sleep 5
  local code; code=$(curl -s -m 3 -o /dev/null -w '%{http_code}' localhost:18090/api/healthz) || true
  echo "    via port-forward after delete: HTTP $code (000 = connection failed: the tunnel died with the pod)"
  kill "$pf" 2>/dev/null || true
  incluster gateway-prod 'curl -s -o /dev/null -w "    via Service: HTTP %{http_code}\n" http://gw-gateway/api/healthz'
}
do_fix() { k -n gateway-prod get pods -l app=gw-gateway; }
