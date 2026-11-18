#!/usr/bin/env bash
# Idempotent bring-up of the local platform. Safe to re-run: every step first checks
# what already exists and skips it. It never upgrades or reinstalls Jenkins.
#
#   cp .env.example .env   # fill GITHUB_USER / GITHUB_PAT once; scripts/lib.sh loads it
#   make up
# Env:
#   GITHUB_USER / GITHUB_PAT  -> k8s Secrets for Kaniko push, GitOps bump, GHCR pull (optional)
#   GITOPS_REPO_URL           -> repo ArgoCD syncs from (default: GitHub mlops-router-gitops)
#   GITOPS_REVISION           -> branch ArgoCD follows (default: main)
#   APP_REPO_URL / APP_BRANCH -> repo + branch the Jenkins jobs build (see scripts/jenkins-configure.sh)
set -euo pipefail
source "$(dirname "$0")/lib.sh"

ARGOCD_CHART_VERSION=10.18.0
JENKINS_CHART_VERSION=5.12.0
GITOPS_REPO_URL=${GITOPS_REPO_URL:-https://github.com/ducthangng/mlops-router-gitops}
GITOPS_REVISION=${GITOPS_REVISION:-main}
ENVS=(dev prod)

step_tools() {
  log "tools"
  for t in docker kind kubectl helm curl base64; do need "$t"; done
  docker info >/dev/null 2>&1 || die "Docker is not running (macOS: open -a Docker)"
  ok "docker, kind, kubectl, helm"
  [[ -d $GITOPS_DIR/charts/router ]] || die "GitOps repo not found at $GITOPS_DIR (set GITOPS_DIR)"
}

step_cluster() {
  log "kind cluster '$CLUSTER'"
  if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
    skip "cluster exists"
  else
    kind create cluster --config "$ROOT/kind-config.yaml"
  fi
  k wait --for=condition=Ready nodes --all --timeout=180s >/dev/null && ok "nodes Ready"
}

step_argocd() {
  log "ArgoCD"
  if h -n argocd status argocd >/dev/null 2>&1; then
    skip "release 'argocd' exists"
  else
    helm repo add argo https://argoproj.github.io/argo-helm >/dev/null 2>&1 || true
    h install argocd argo/argo-cd --version "$ARGOCD_CHART_VERSION" -n argocd --create-namespace --wait
  fi
  k -n argocd rollout status deploy/argocd-server --timeout=300s >/dev/null && ok "argocd-server ready"
}

step_jenkins() {
  log "Jenkins"
  if h -n jenkins status jenkins >/dev/null 2>&1; then
    skip "release 'jenkins' exists (never upgraded here: plugins stay as installed)"
  else
    helm repo add jenkins https://charts.jenkins.io >/dev/null 2>&1 || true
    h install jenkins jenkins/jenkins --version "$JENKINS_CHART_VERSION" -n jenkins --create-namespace \
      -f "$GITOPS_DIR/jenkins/values.yaml"
  fi
  heal_jenkins_init
  k -n jenkins wait --for=condition=Ready pod/jenkins-0 --timeout=600s >/dev/null && ok "jenkins-0 ready"
}

# Incident (NOTES.md): after Docker Desktop restarts, the pod sandbox is recreated but keeps its
# emptyDirs. The chart's init script runs `yes n | cp -i ...` under `set -e`; coreutils >= 9.2
# returns 1 when cp -i skips a file, so init crash-loops forever. A new pod gets fresh emptyDirs;
# jenkins_home is a PVC, so nothing is lost.
heal_jenkins_init() {
  local reason
  reason=$(k -n jenkins get pod jenkins-0 \
    -o jsonpath='{.status.initContainerStatuses[?(@.name=="init")].state.waiting.reason}' 2>/dev/null || true)
  if [[ $reason == CrashLoopBackOff ]]; then
    warn "jenkins-0 init is crash-looping (stale emptyDir after a node restart): recreating the pod"
    k -n jenkins delete pod jenkins-0 --wait=true
    sleep 5
  fi
}

step_namespaces() {
  log "namespaces"
  for ns in "${ENVS[@]/#/router-}"; do
    k create namespace "$ns" --dry-run=client -o yaml | k apply -f - >/dev/null && ok "$ns"
  done
}

dockerconfig() {
  local auth
  auth=$(printf '%s:%s' "$GITHUB_USER" "$GITHUB_PAT" | base64 | tr -d '\n')
  printf '{"auths":{"ghcr.io":{"auth":"%s"}}}' "$auth"
}

step_secrets() {
  log "secrets (GITHUB_USER / GITHUB_PAT from .env, never from Git)"
  if [[ -z ${GITHUB_USER:-} || -z ${GITHUB_PAT:-} ]]; then
    warn "GITHUB_USER/GITHUB_PAT not set in .env: skipping. Without them Kaniko cannot push and CI cannot bump GitOps."
    return 0
  fi
  # Process substitution keeps the token out of argv (ps) and out of shell history.
  k -n jenkins create secret generic ghcr-creds --type=kubernetes.io/dockerconfigjson \
    --from-file=.dockerconfigjson=<(dockerconfig) --dry-run=client -o yaml | k apply -f - >/dev/null
  ok "jenkins/ghcr-creds (Kaniko push)"
  k -n jenkins create secret generic github-pat \
    --from-file=username=<(printf %s "$GITHUB_USER") --from-file=password=<(printf %s "$GITHUB_PAT") \
    --dry-run=client -o yaml | k apply -f - >/dev/null
  ok "jenkins/github-pat (source for the Jenkins credentials)"
  for ns in "${ENVS[@]/#/router-}"; do
    k -n "$ns" create secret generic ghcr-pull --type=kubernetes.io/dockerconfigjson \
      --from-file=.dockerconfigjson=<(dockerconfig) --dry-run=client -o yaml | k apply -f - >/dev/null
    ok "$ns/ghcr-pull (only used if the GHCR package is private)"
  done
}

step_jenkins_config() {
  log "Jenkins configuration as code"
  if [[ -x $ROOT/scripts/jenkins-configure.sh ]]; then
    "$ROOT/scripts/jenkins-configure.sh"
  else
    skip "scripts/jenkins-configure.sh not present"
  fi
}

step_apps() {
  log "ArgoCD applications ($GITOPS_REPO_URL @ $GITOPS_REVISION)"
  for env in "${ENVS[@]}"; do
    sed -e "s|repoURL: .*|repoURL: $GITOPS_REPO_URL|" -e "s|targetRevision: .*|targetRevision: $GITOPS_REVISION|" \
      "$GITOPS_DIR/argocd/gw-$env.yaml" | k apply -f - >/dev/null
    ok "gw-$env -> router-$env"
  done
  if k -n argocd get application gw >/dev/null 2>&1; then
    warn "legacy app 'gw' (ns default) still exists. Retire it with: make retire-gw"
  fi
}

summary() {
  local argo_pw jenkins_pw
  argo_pw=$(secret_value argocd argocd-initial-admin-secret password || true)
  jenkins_pw=$(secret_value jenkins jenkins jenkins-admin-password || true)
  cat <<EOF

$(log "ready")
  ArgoCD   https://localhost:8080   kubectl --context $CTX -n argocd port-forward svc/argocd-server 8080:443
           user admin / ${argo_pw:-<argocd-initial-admin-secret not found>}
  Jenkins  http://localhost:8081    kubectl --context $CTX -n jenkins port-forward svc/jenkins 8081:8080
           user admin / ${jenkins_pw:-<not found>}
  gw dev   http://localhost:8000    kubectl --context $CTX -n router-dev port-forward svc/gw-router 8000:80
  gw prod  http://localhost:8002    kubectl --context $CTX -n router-prod port-forward svc/gw-router 8002:80
  (port-forward tunnels to ONE pod, not the Service: lesson 1)

  curl -s localhost:8000/v1/chat/completions -H 'content-type: application/json' -H 'X-API-Key: demo' \\
       -d '{"messages":[{"role":"user","content":"why does my go program deadlock"}]}' -D - -o /dev/null
EOF
}

main() {
  step_tools
  step_cluster
  step_argocd
  step_jenkins
  step_namespaces
  step_secrets
  step_jenkins_config
  step_apps
  summary
}

main "$@"
