#!/usr/bin/env bash
# Jenkins configuration as code WITHOUT `helm upgrade` and WITHOUT touching plugins.
# Idempotent: re-running converges to the same state.
#
#  1. check the installed plugins provide what we use (job-dsl absent -> jobs via REST + config.xml)
#  2. JCasC: ConfigMap labelled for the chart's config-reload sidecar (hot reload, no restart)
#  3. credentials github-pat + ghcr-creds: synced from k8s Secret jenkins/github-pat
#  4. jobs router (multibranch) + qualify (pipeline): create or update from committed config.xml
#
# Env: APP_REPO_URL (default GitHub mlops-skeleton), APP_BRANCH (qualify job, default main)
set -euo pipefail
source "$(dirname "$0")/lib.sh"

APP_REPO_URL=${APP_REPO_URL:-https://github.com/ducthangng/mlops-skeleton.git}
APP_BRANCH=${APP_BRANCH:-main}
JENKINS_DIR=$GITOPS_DIR/jenkins
REQUIRED_PLUGINS=(configuration-as-code workflow-multibranch workflow-aggregator git kubernetes credentials
                  credentials-binding pipeline-model-definition)

# Replacement strings are quoted on purpose: in bash >= 5.2 an unquoted `&` in ${v//pat/repl}
# expands to the matched text (patsub_replacement), which silently corrupted the XML.
xml_escape() {
  local s=${1//&/"&amp;"}; s=${s//</"&lt;"}; s=${s//>/"&gt;"}; printf '%s' "$s"
}

check_plugins() {
  log "installed plugins"
  local installed missing=()
  installed=$(jenkins_api GET '/pluginManager/api/json?tree=plugins%5BshortName%5D' </dev/null | tail -n +2 \
              | grep -o '"shortName":"[^"]*"' | cut -d'"' -f4)
  for p in "${REQUIRED_PLUGINS[@]}"; do grep -qx "$p" <<<"$installed" || missing+=("$p"); done
  [[ ${#missing[@]} -eq 0 ]] || die "missing plugins: ${missing[*]} (not installing them here, on purpose)"
  ok "${#REQUIRED_PLUGINS[@]} required plugins present ($(wc -l <<<"$installed" | tr -d ' ') installed)"
  for p in job-dsl junit pipeline-utility-steps; do
    grep -qx "$p" <<<"$installed" && ok "optional $p present" || skip "optional $p absent (not used)"
  done
}

apply_casc() {
  log "JCasC ConfigMap -> config-reload sidecar"
  k -n jenkins create configmap jenkins-casc-ci --from-file="$JENKINS_DIR/casc/" --dry-run=client -o yaml \
    | k label --local -f - jenkins-jenkins-config=true -o yaml | k apply -f - >/dev/null
  ok "configmap jenkins-casc-ci applied"
  local i msg
  for i in $(seq 1 30); do       # the sidecar polls the API and POSTs /reload-configuration-as-code
    msg=$(jenkins_api GET '/api/json?tree=description' </dev/null | tail -n +2)
    grep -q 'Configured as code' <<<"$msg" && break
    sleep 2
  done
  grep -q 'Configured as code' <<<"$msg" && ok "reloaded (system message present)" || warn "reload not observed yet"
}

upsert_credential() {  # id description username password
  local tpl xml code
  tpl=$(<"$JENKINS_DIR/jobs/credential.xml.tpl")
  local user pass
  user=$(xml_escape "$3"); pass=$(xml_escape "$4")
  xml=${tpl//@@ID@@/"$1"}; xml=${xml//@@DESCRIPTION@@/"$2"}
  xml=${xml//@@USERNAME@@/"$user"}; xml=${xml//@@PASSWORD@@/"$pass"}
  code=$(jenkins_api POST "/credentials/store/system/domain/_/credential/$1/config.xml" <<<"$xml" | http_code)
  if [[ $code == 404 ]]; then
    code=$(jenkins_api POST /credentials/store/system/domain/_/createCredentials <<<"$xml" | http_code)
  fi
  [[ $code == 200 ]] || die "credential $1: HTTP $code"   # body not printed: it may echo the secret
  ok "credential $1"
}

sync_credentials() {
  log "credentials (from k8s Secret jenkins/github-pat)"
  if ! k -n jenkins get secret github-pat >/dev/null 2>&1; then
    skip "secret jenkins/github-pat missing (set GITHUB_USER/GITHUB_PAT and re-run make up)"
    return 0
  fi
  local user pat
  user=$(secret_value jenkins github-pat username); pat=$(secret_value jenkins github-pat password)
  upsert_credential github-pat "GitHub PAT: clone + push the GitOps repo" "$user" "$pat"
  upsert_credential ghcr-creds "GHCR login (same PAT, write:packages)" "$user" "$pat"
}

credential_exists() {
  [[ $(jenkins_api GET "/credentials/store/system/domain/_/credential/$1/api/json" </dev/null | http_code) == 200 ]]
}

upsert_job() {  # name xml
  local code
  if [[ $(jenkins_api GET "/job/$1/api/json?tree=name" </dev/null | http_code) == 200 ]]; then
    code=$(jenkins_api POST "/job/$1/config.xml" <<<"$2" | http_code); ok "job $1 updated (HTTP $code)"
  else
    code=$(jenkins_api POST "/createItem?name=$1" <<<"$2" | http_code); ok "job $1 created (HTTP $code)"
  fi
  [[ $code == 200 ]] || die "job $1: HTTP $code"
}

apply_jobs() {
  log "jobs ($APP_REPO_URL, qualify on $APP_BRANCH)"
  k apply -f "$JENKINS_DIR/qualify-cache-pvc.yaml" >/dev/null && ok "pvc jenkins/qualify-cache (qualify stage cache)"
  local cred="" xml
  credential_exists github-pat && cred=github-pat
  [[ -n $cred ]] || warn "no github-pat credential: jobs clone anonymously (fine for a public repo / local git daemon)"
  for job in router qualify; do
    xml=$(<"$JENKINS_DIR/jobs/$job.xml")
    xml=${xml//@@APP_REPO_URL@@/"$(xml_escape "$APP_REPO_URL")"}; xml=${xml//@@APP_BRANCH@@/"$APP_BRANCH"}
    xml=${xml//@@CREDENTIALS_ID@@/"$cred"}
    upsert_job "$job" "$xml"
  done
  jenkins_api POST /job/router/build?delay=0 </dev/null >/dev/null && ok "router: branch indexing triggered"
}

verify() {
  log "verify"
  jenkins_api GET '/api/json?tree=numExecutors,jobs%5Bname%5D' </dev/null | tail -n +2; echo
}

main() {
  k -n jenkins wait --for=condition=Ready pod/jenkins-0 --timeout=600s >/dev/null
  check_plugins
  apply_casc
  sync_credentials
  apply_jobs
  verify
}

main "$@"
