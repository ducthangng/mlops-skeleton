#!/usr/bin/env bash
# Delete Jenkins build history (builds only: jobs, config, credentials and plugins stay).
# Job build numbers restart at #1 when nothing is left running.
#
#   scripts/jenkins-clean.sh                 # dry run: what would be deleted
#   CONFIRM=1 scripts/jenkins-clean.sh       # delete builds of every job
#   CONFIRM=1 JOB=ai-gateway/main scripts/jenkins-clean.sh   # one job (or a folder: JOB=ai-gateway)
set -euo pipefail
source "$(dirname "$0")/lib.sh"

dry=true; [[ ${CONFIRM:-} == 1 ]] && dry=false
read -r -d '' GROOVY <<EOF || true
import jenkins.model.Jenkins
import hudson.model.Job
def only = '${JOB:-}'
boolean dry = ${dry}
int total = 0
Jenkins.instance.getAllItems(Job).findAll { !only || it.fullName == only || it.fullName.startsWith(only + '/') }
  .sort { it.fullName }.each { j ->
    def done = j.builds.toList().findAll { !it.isBuilding() }
    if (!dry) {
      done.each { it.delete() }
      if (j.builds.isEmpty()) j.updateNextBuildNumber(1)
    }
    total += done.size()
    println String.format('    %-40s %3d build(s)', java.net.URLDecoder.decode(j.fullName, 'UTF-8'), done.size())
}
println(dry ? "    dry run: \${total} build(s) would be deleted. Re-run with CONFIRM=1." : "    deleted \${total} build(s)")
EOF

log "Jenkins build history${JOB:+ (job $JOB)}"
body="script=$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.stdin.read()))' <<<"$GROOVY")"
out=$(jenkins_api POST /scriptText application/x-www-form-urlencoded <<<"$body")
[[ ${out%%$'\n'*} == 200 ]] || die "script console -> HTTP ${out%%$'\n'*}"
printf '%s\n' "${out#*$'\n'}"
