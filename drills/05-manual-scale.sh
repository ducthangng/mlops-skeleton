TITLE="Manual kubectl scale -> ArgoCD selfHeal puts Git's value back"
BREAKS="kubectl scale deploy/gw-gateway --replicas=4 in gateway-dev (Git says 1)"
OBSERVE=("replicas go 4 -> back to Git's value within seconds (printed above)"
         "ArgoCD UI: gw-dev briefly OutOfSync, then Synced (selfHeal)"
         "to change replicas for real: edit values-dev.yaml and commit")
RESTORE="nothing to do: ArgoCD already restored it"
do_break() {
  local want; want=$(k -n gateway-dev get deploy gw-gateway -o jsonpath='{.spec.replicas}')
  k -n gateway-dev scale deploy gw-gateway --replicas=4 >/dev/null && ok "scaled to 4 (Git: $want)"
  for i in $(seq 1 24); do
    local now; now=$(k -n gateway-dev get deploy gw-gateway -o jsonpath='{.spec.replicas}')
    echo "    t=$((i * 5))s spec.replicas=$now"
    [[ $now == "$want" ]] && { ok "selfHeal restored replicas=$want"; return; }
    sleep 5
  done
  warn "not restored after 2 min: is selfHeal on? (kubectl -n argocd get app gw-dev -o yaml)"
}
do_fix() { k -n gateway-dev get deploy gw-gateway; }
