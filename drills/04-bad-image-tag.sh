TITLE="Bad image tag in Git -> ImagePullBackOff, old pods keep serving; rollback = git revert"
BREAKS="a GitOps commit sets charts/ai-gateway/values-dev.yaml image.tag to a tag that does not exist"
OBSERVE=("kubectl -n gateway-dev get pods -w   -> new pod ErrImagePull / ImagePullBackOff, old pod stays Running"
         "ArgoCD gw-dev: Synced but Progressing/Degraded; the Service still answers (checked below after sync)"
         "rollout cannot finish: the new pod never becomes Ready, so the old ReplicaSet is not scaled down (lesson 2)")
RESTORE="git revert of the drill commit in the GitOps repo (+ push): ArgoCD rolls back on its own"
do_break() {
  local f=$GITOPS_DIR/charts/ai-gateway/values-dev.yaml
  sed -i.bak 's|^  tag: .*|  tag: "does-not-exist"|' "$f" && rm "$f.bak"
  git -C "$GITOPS_DIR" -c user.name=drill -c user.email=drill@local commit -qm "drill: bad image tag" "$f"
  ok "committed in $GITOPS_DIR ($(git -C "$GITOPS_DIR" rev-parse --abbrev-ref HEAD))"
  if [[ ${PUSH:-0} == 1 ]]; then git -C "$GITOPS_DIR" push; else echo "    push it: git -C $GITOPS_DIR push   (or PUSH=1)"; fi
  k -n argocd annotate application gw-dev argocd.argoproj.io/refresh=normal --overwrite >/dev/null
  for _ in $(seq 1 24); do
    k -n gateway-dev get pods -l app=gw-gateway --no-headers 2>/dev/null | grep -qE 'ErrImagePull|ImagePullBackOff' && break
    sleep 5
  done
  k -n gateway-dev get pods -l app=gw-gateway
  incluster gateway-dev 'curl -s -o /dev/null -w "Service still answers: HTTP %{http_code}\n" http://gw-gateway/api/healthz'
}
do_fix() {
  local sha
  sha=$(git -C "$GITOPS_DIR" log --format=%h --grep '^drill: bad image tag' -n 1)
  [[ -n $sha ]] || die "no 'drill: bad image tag' commit found"
  git -C "$GITOPS_DIR" -c user.name=drill -c user.email=drill@local revert --no-edit "$sha" >/dev/null
  ok "reverted $sha"
  if [[ ${PUSH:-0} == 1 ]]; then git -C "$GITOPS_DIR" push; else echo "    push it: git -C $GITOPS_DIR push   (or PUSH=1)"; fi
  k -n argocd annotate application gw-dev argocd.argoproj.io/refresh=normal --overwrite >/dev/null
  k -n gateway-dev rollout status deploy/gw-gateway --timeout=180s
}
