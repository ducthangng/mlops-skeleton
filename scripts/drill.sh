#!/usr/bin/env bash
# make drill-NN      -> scripts/drill.sh NN break
# make drill-NN-fix  -> scripts/drill.sh NN fix
# make drills        -> list
#
# Each drills/NN-name.sh defines TITLE, BREAKS, OBSERVE, RESTORE and do_break / do_fix.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
source "$ROOT/drills/lib.sh"

if [[ ${1:-list} == list ]]; then
  for f in "$ROOT"/drills/[0-9][0-9]-*.sh; do
    TITLE=""; source "$f"; printf '  make drill-%s   %s\n' "$(basename "$f" | cut -c1-2)" "$TITLE"
  done
  exit 0
fi

nn=$1 action=${2:-break}
file=$(ls "$ROOT"/drills/"$nn"-*.sh 2>/dev/null | head -1)
[[ -n $file ]] || die "no drill $nn (make drills)"
source "$file"

say "drill $nn: $TITLE"
if [[ $action == break ]]; then
  echo "  what breaks: $BREAKS"
  do_break
  printf '\033[36m  what to observe:\033[0m\n'; printf '    %s\n' "${OBSERVE[@]}"
  echo "  restore: make drill-$nn-fix"
else
  echo "  restore: $RESTORE"
  do_fix
fi
