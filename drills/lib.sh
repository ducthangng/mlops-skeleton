# Helpers for drills/NN-*.sh. Sourced by scripts/drill.sh (which also sources scripts/lib.sh).
# shellcheck shell=bash

DRILL_NS=drills                       # NOT managed by ArgoCD: selfHeal would revert our breakage
WORKTREES=$ROOT/.drills               # gitignored; drill branches live in their own worktrees

say()     { printf '\n\033[1m%s\033[0m\n' "$*"; }
observe() { printf '\033[36m  observe:\033[0m %s\n' "$*"; }

# Run a shell snippet in a throwaway pod inside the cluster (talks to Services, not port-forwards).
# `sleep 1` first: with `kubectl run -i`, output of a container that exits before kubectl attaches is lost.
incluster() {  # incluster <namespace> '<sh snippet>'
  k -n "$1" run "drill-curl-$RANDOM" --rm -i --restart=Never --quiet --image=curlimages/curl:8.11.1 \
    --command -- sh -c "sleep 1; $2" 2>&1 | { grep -v -E '^pod ".*" deleted|If you don.t see a command prompt' || true; }
}

chat_json() { printf '{"messages":[{"role":"user","content":"%s"}]}' "$1"; }

# --- Git drills: a branch in its own worktree; your working tree is never touched ---------------
drill_branch() {  # drill_branch <name> -> prints the worktree path
  local name=$1 dir=$WORKTREES/$1
  git -C "$ROOT" worktree remove --force "$dir" 2>/dev/null || true
  git -C "$ROOT" branch -D "drill/$name" >/dev/null 2>&1 || true
  git -C "$ROOT" worktree add -q -b "drill/$name" "$dir" HEAD
  printf '%s' "$dir"
}

drill_commit() {  # drill_commit <worktree> <message>
  git -C "$1" -c user.name=drill -c user.email=drill@local commit -qam "drill: $2"
}

drill_branch_remove() {
  git -C "$ROOT" worktree remove --force "$WORKTREES/$1" 2>/dev/null || true
  git -C "$ROOT" branch -D "drill/$1" >/dev/null 2>&1 && ok "deleted local branch drill/$1" || skip "no branch drill/$1"
  echo "    if you pushed it: git push origin --delete drill/$1"
}

push_hint() {
  echo "    see it in Jenkins: git -C $ROOT push origin drill/$1   (job 'router' indexes branches every 2 min)"
}

# --- Cluster drills: a separate Helm release in ns 'drills', same image as router-dev -----------
dev_image_tag() {
  k -n router-dev get deploy gw-router -o jsonpath='{.spec.template.spec.containers[0].image}' | sed 's/.*://'
}

drills_release() {  # drills_release [--set k=v ...]   (always starts from values-dev.yaml + current dev image)
  h upgrade --install gw "$GITOPS_DIR/charts/router" -n "$DRILL_NS" --create-namespace \
    -f "$GITOPS_DIR/charts/router/values-dev.yaml" --set image.tag="$(dev_image_tag)" "$@" \
    --wait=hookOnly >/dev/null
}

drills_ready() {
  k -n "$DRILL_NS" rollout status deploy/gw-router --timeout=120s >/dev/null &&
  k -n "$DRILL_NS" rollout status deploy/gw-mock-llm --timeout=120s >/dev/null &&
  k -n "$DRILL_NS" rollout status deploy/gw-redis --timeout=120s >/dev/null
}

# --- Jenkins ------------------------------------------------------------------------------------
jenkins_build() {  # jenkins_build <job url path> [query]   e.g. /job/router/job/main 'KANIKO_MEMORY=150Mi'
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
