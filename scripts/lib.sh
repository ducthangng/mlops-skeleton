# Shared helpers for scripts/*.sh and drills/*.sh. Source it, don't run it.
# shellcheck shell=bash

ROOT=${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
# Local settings and secrets live in $ROOT/.env (gitignored; template: .env.example).
# Values already set in the environment win over the file.
if [[ -f $ROOT/.env ]]; then
  while IFS="=" read -r _k _v || [[ -n $_k ]]; do
    [[ $_k =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue          # skip comments / blank lines
    [[ -n ${!_k:-} ]] && continue
    _v=${_v%$'\r'}; _v=${_v#[\"\']}; _v=${_v%[\"\']}
    export "$_k=$_v"
  done < "$ROOT/.env"
  unset _k _v
fi

GITOPS_DIR=${GITOPS_DIR:-$ROOT/../mlops-gitops}
APP_DIR=${APP_DIR:-$ROOT/../custom-ai-gateway}     # product repo (gateway/ + web/)
CLUSTER=${CLUSTER:-mlops}
CTX="kind-$CLUSTER"

# Always pass the context explicitly: never touch whatever cluster happens to be current.
k() { kubectl --context "$CTX" "$@"; }
h() { helm --kube-context "$CTX" "$@"; }

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '    \033[32mok\033[0m   %s\n' "$*"; }
skip() { printf '    \033[2mskip %s\033[0m\n' "$*"; }
warn() { printf '    \033[33mwarn\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31mERROR\033[0m %s\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "missing tool: $1"; }

secret_value() {  # secret_value <ns> <name> <key>
  k -n "$1" get secret "$2" -o "jsonpath={.data.$3}" 2>/dev/null | base64 -d
}

# Runs curl INSIDE the controller pod: the admin password never leaves the pod (it is read from
# the file the chart already mounts). Request body comes from stdin, so no secret is in argv.
#   jenkins_api METHOD PATH [CONTENT_TYPE] < body      -> prints "HTTP_CODE" then response body
read -r -d '' JENKINS_IN_POD <<'SH' || true
set -euo pipefail
method=$1 path=$2 ctype=${3:-application/xml}
netrc=$(mktemp) jar=$(mktemp) out=$(mktemp)
trap 'rm -f "$netrc" "$jar" "$out"' EXIT
printf 'machine localhost login %s password %s\n' \
  "$(cat /run/secrets/additional/chart-admin-username)" "$(cat /run/secrets/additional/chart-admin-password)" > "$netrc"
base=http://localhost:8080
if [ "$method" = GET ]; then
  code=$(curl -s -o "$out" -w '%{http_code}' --netrc-file "$netrc" "$base$path")
else
  crumb=$(curl -sf --netrc-file "$netrc" -c "$jar" "$base/crumbIssuer/api/json" \
          | sed -E 's/.*"crumb":"([^"]+)".*"crumbRequestField":"([^"]+)".*/\2:\1/')
  code=$(curl -s -o "$out" -w '%{http_code}' --netrc-file "$netrc" -b "$jar" -H "$crumb" \
          -X "$method" -H "Content-Type: $ctype" --data-binary @- "$base$path")
fi
echo "$code"; cat "$out"
SH

jenkins_api() { k -n jenkins exec -i jenkins-0 -c jenkins -- bash -c "$JENKINS_IN_POD" _ "$@"; }
# Read the status line AND drain the rest: `head -n1` would close the pipe early, the producer gets
# SIGPIPE (exit 141) and, under pipefail + set -e, the script dies without a message.
http_code() { local first; IFS= read -r first; cat >/dev/null; printf '%s' "$first"; }
