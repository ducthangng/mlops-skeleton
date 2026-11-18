# Shared helpers for scripts/*.sh and drills/*.sh. Source it, don't run it.
# shellcheck shell=bash

ROOT=${ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
GITOPS_DIR=${GITOPS_DIR:-$ROOT/../mlops-gitops}
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
