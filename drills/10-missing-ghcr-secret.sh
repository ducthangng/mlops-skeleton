TITLE="Missing ghcr-creds Secret -> branch builds still pass, main fails at push (auth)"
BREAKS="delete Secret jenkins/ghcr-creds (Kaniko's registry login)"
OBSERVE=("branch builds: green (the volume is optional and branches build with --no-push)"
         "main build: 'Build image' fails with UNAUTHORIZED / 401 from ghcr.io -> logtriage category auth"
         "nothing is deployed: the GitOps bump stage never runs"
         "before the Secret was optional, the agent pod hung in ContainerCreating (samples/real_aborted_pod_pending.log)")
RESTORE="re-create it from env: GITHUB_USER=... GITHUB_PAT=... make up (idempotent)"
do_break() {
  k -n jenkins delete secret ghcr-creds --ignore-not-found
  if [[ ${TRIGGER:-0} == 1 ]]; then
    local n r; n=$(jenkins_build /job/router/job/main); r=$(jenkins_wait /job/router/job/main "$n")
    echo "    router/main #$n: $r"; triage_build /job/router/job/main "$n"
  else
    echo "    trigger a main build: TRIGGER=1 make drill-10 (or push to main)"
  fi
}
do_fix() {
  [[ -n ${GITHUB_USER:-} && -n ${GITHUB_PAT:-} ]] || die "set GITHUB_USER and GITHUB_PAT, then re-run"
  "$ROOT/scripts/bootstrap.sh" >/dev/null && ok "secrets re-created"
}
