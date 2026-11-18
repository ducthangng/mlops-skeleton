TITLE="Kaniko memory limit too low -> OOMKilled (exit 137)"
BREAKS="build a product branch with parameter KANIKO_MEMORY=${KANIKO_MEMORY:-150Mi}"
OBSERVE=("'Build image' fails; the kaniko container is OOMKilled (exit 137)"
         "logtriage: category oom with the evidence line (printed above)"
         "the default is 2Gi because the image also runs npm ci + vite build (web UI)")
RESTORE="nothing: the next build uses the default KANIKO_MEMORY=2Gi"
do_break() {
  local branch path n r
  branch=${BRANCH:-$(git -C "$APP_DIR" rev-parse --abbrev-ref HEAD)}
  path="/job/ai-gateway/job/$(python3 -c 'import sys,urllib.parse as u; print(u.quote(u.quote(sys.argv[1], safe=""), safe=""))' "$branch")"
  n=$(jenkins_build "$path" "KANIKO_MEMORY=${KANIKO_MEMORY:-150Mi}")
  echo "    ai-gateway/$branch #$n running (the branch must be known to Jenkins, i.e. pushed)"
  r=$(jenkins_wait "$path" "$n"); echo "    result: $r"
  triage_build "$path" "$n"
}
do_fix() { echo "    default KANIKO_MEMORY is 2Gi"; }
