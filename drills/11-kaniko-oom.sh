TITLE="Kaniko memory limit too low -> OOMKilled (exit 137)"
BREAKS="build the current branch with parameter KANIKO_MEMORY=${KANIKO_MEMORY:-150Mi}"
OBSERVE=("'Build image' fails; the kaniko container is OOMKilled (exit 137)"
         "logtriage: category oom with the evidence line (printed above)"
         "history: with PyTorch in the image this happened even at 4Gi; the slim image builds with 1Gi")
RESTORE="nothing: the next build uses the default KANIKO_MEMORY=1Gi"
do_break() {
  local branch path n r
  branch=${BRANCH:-$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)}
  path="/job/router/job/$(python3 -c 'import sys,urllib.parse as u; print(u.quote(u.quote(sys.argv[1], safe=""), safe=""))' "$branch")"
  n=$(jenkins_build "$path" "KANIKO_MEMORY=${KANIKO_MEMORY:-150Mi}")
  echo "    router/$branch #$n running (the branch must be known to Jenkins, i.e. pushed)"
  r=$(jenkins_wait "$path" "$n"); echo "    result: $r"
  triage_build "$path" "$n"
}
do_fix() { echo "    default KANIKO_MEMORY is 1Gi"; }
