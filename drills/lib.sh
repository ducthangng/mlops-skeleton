# Helpers for drills/NN-*.sh. Sourced by scripts/drill.sh (which also sources scripts/lib.sh).
# shellcheck shell=bash

DRILL_NS=drills                       # NOT managed by ArgoCD: selfHeal would revert our breakage
WORKTREES=$ROOT/.drills               # gitignored; drill branches of the PRODUCT repo live here as worktrees

say()     { printf '\n\033[1m%s\033[0m\n' "$*"; }
observe() { printf '\033[36m  observe:\033[0m %s\n' "$*"; }

# Run a shell snippet in a throwaway pod inside the cluster (talks to Services, not port-forwards).
# `sleep 1` first: with `kubectl run -i`, output of a container that exits before kubectl attaches is lost.
incluster() {  # incluster <namespace> '<sh snippet>'
  k -n "$1" run "drill-curl-$RANDOM" --rm -i --restart=Never --quiet --image=curlimages/curl:8.11.1 \
    --command -- sh -c "sleep 1; $2" 2>&1 | { grep -v -E '^pod ".*" deleted|If you don.t see a command prompt' || true; }
}

# POST /api/chat body (mock provider unless overridden). Usage: chat_json "<message>" ['"routing":"manual",...']
chat_json() { printf '{"message":"%s","settings":{"provider":"mock","model":"echo"%s}}' "$1" "${2:+,$2}"; }

# Prepend to an incluster snippet: `chat <label> '<json>'` sends one chat to gw-gateway and prints one line:
# HTTP code, total time, the last SSE event, and routed_by / cache_status / status / error message.
CHAT_SH='chat() { curl -s -m 30 -o /tmp/sse -w "%{http_code} %{time_total}" http://gw-gateway/api/chat -H "content-type: application/json" -d "$2" >/tmp/meta; printf "    %-10s http=%s t=%ss last=%s %s\n" "$1" "$(cut -d" " -f1 /tmp/meta)" "$(cut -d" " -f2 /tmp/meta)" "$(grep -E "^event:" /tmp/sse | tail -1 | cut -c8-)" "$(grep -oE "\"(routed_by|cache_status|stop_reason|message)\": ?\"[^\"]*\"" /tmp/sse | sort -u | tr "\n" " " | cut -c1-200)"; }; '

# --- Git drills: a branch of the PRODUCT repo in its own worktree; your working tree is never touched ---
drill_branch() {  # drill_branch <name> -> prints the worktree path
  local name=$1 dir=$WORKTREES/$1
  [[ -d $APP_DIR/.git ]] || die "product repo not found at $APP_DIR (set APP_DIR)"
  git -C "$APP_DIR" worktree remove --force "$dir" 2>/dev/null || true
  git -C "$APP_DIR" branch -D "drill/$name" >/dev/null 2>&1 || true
  git -C "$APP_DIR" worktree add -q -b "drill/$name" "$dir" HEAD
  printf '%s' "$dir"
}

drill_commit() {  # drill_commit <worktree> <message>
  git -C "$1" -c user.name=drill -c user.email=drill@local commit -qam "drill: $2"
}

drill_branch_remove() {
  git -C "$APP_DIR" worktree remove --force "$WORKTREES/$1" 2>/dev/null || true
  git -C "$APP_DIR" branch -D "drill/$1" >/dev/null 2>&1 && ok "deleted local branch drill/$1" || skip "no branch drill/$1"
  echo "    if you pushed it: git -C $APP_DIR push origin --delete drill/$1"
}

push_hint() {
  echo "    see it in Jenkins: git -C $APP_DIR push origin drill/$1   (job 'ai-gateway' indexes branches every 2 min)"
}

# --- Cluster drills: a separate Helm release in ns 'drills', same image as gateway-dev ----------------
# DRILL_IMAGE=repo:tag overrides it (e.g. an image loaded with `kind load docker-image`, pulled with Never).
drill_image_args() {
  if [[ -n ${DRILL_IMAGE:-} ]]; then
    printf '%s\n' --set "image.repository=${DRILL_IMAGE%:*}" --set "image.tag=${DRILL_IMAGE##*:}" --set image.pullPolicy=Never
  else
    local img; img=$(k -n gateway-dev get deploy gw-gateway -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null) \
      || die "no gw-gateway in gateway-dev yet (first CI build not deployed); or set DRILL_IMAGE"
    printf '%s\n' --set "image.tag=${img##*:}"
  fi
}

drill_secrets() {  # the chart needs postgres-auth (+ ghcr-pull for a private GHCR package)
  k create namespace "$DRILL_NS" --dry-run=client -o yaml | k apply -f - >/dev/null
  k -n "$DRILL_NS" get secret postgres-auth >/dev/null 2>&1 ||
    k -n "$DRILL_NS" create secret generic postgres-auth --from-file=password=<(openssl rand -hex 24 | tr -d '\n') >/dev/null
  if ! k -n "$DRILL_NS" get secret ghcr-pull >/dev/null 2>&1 && k -n gateway-dev get secret ghcr-pull >/dev/null 2>&1; then
    k -n "$DRILL_NS" create secret generic ghcr-pull --type=kubernetes.io/dockerconfigjson --from-file=.dockerconfigjson=<(
      k -n gateway-dev get secret ghcr-pull -o go-template='{{index .data ".dockerconfigjson" | base64decode}}') >/dev/null
  fi
}

drills_release() {  # drills_release [--set k=v ...]   values-dev.yaml + dev image; never the real provider keys
  local img=()
  mapfile -t img < <(drill_image_args)
  drill_secrets
  h upgrade --install gw "$GITOPS_DIR/charts/ai-gateway" -n "$DRILL_NS" \
    -f "$GITOPS_DIR/charts/ai-gateway/values-dev.yaml" "${img[@]}" \
    --set providerKeysSecret=drill-provider-keys "$@" --wait=hookOnly >/dev/null
}

drills_ready() {
  k -n "$DRILL_NS" rollout status statefulset/gw-postgres --timeout=180s >/dev/null &&
  k -n "$DRILL_NS" rollout status deploy/gw-redis --timeout=120s >/dev/null &&
  k -n "$DRILL_NS" rollout status deploy/gw-gateway --timeout=180s >/dev/null
}

drills_uninstall() {
  h -n "$DRILL_NS" uninstall gw >/dev/null 2>&1 && ok "drills release removed" || skip "nothing installed"
  k -n "$DRILL_NS" delete pvc -l app.kubernetes.io/instance=gw --ignore-not-found >/dev/null 2>&1 || true
  k -n "$DRILL_NS" delete pvc data-gw-postgres-0 --ignore-not-found >/dev/null 2>&1 || true
}

# --- Jenkins ------------------------------------------------------------------------------------
jenkins_build() {  # jenkins_build <job url path> [query]   e.g. /job/ai-gateway/job/main 'KANIKO_MEMORY=150Mi'
  local path=$1 query=${2:-} next
  next=$(jenkins_api GET "$path/api/json?tree=nextBuildNumber" </dev/null | tail -n +2 | sed -E 's/.*"nextBuildNumber":([0-9]+).*/\1/')
  if [[ -n $query ]]; then jenkins_api POST "$path/buildWithParameters?$query" </dev/null >/dev/null
  else jenkins_api POST "$path/build?delay=0" </dev/null >/dev/null; fi
  printf '%s' "$next"
}

jenkins_wait() {  # jenkins_wait <job url path> <build number> -> prints result
  local r
  for _ in $(seq 1 180); do
    r=$(jenkins_api GET "$1/$2/api/json?tree=building,result" </dev/null | tail -n +2)
    grep -q '"building":false' <<<"$r" && { sed -E 's/.*"result":"?([A-Z]+)"?.*/\1/' <<<"$r"; return; }
    sleep 5
  done
  echo TIMEOUT
}

triage_build() {  # triage_build <job url path> <build number>
  local f; f=$(mktemp)
  jenkins_api GET "$1/$2/consoleText" </dev/null | tail -n +2 > "$f"
  (cd "$ROOT" && uv run --quiet python -m tools.logtriage analyze "$f" | tail -n 1)
  rm -f "$f"
}
