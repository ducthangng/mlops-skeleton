# Sample console logs for tests/unit/test_logtriage.py

| file | origin |
|---|---|
| `unknown_git_not_found.log` | real: Jenkins build `router/feat%2Fcv-parity #2` (`git` missing in the python image) |
| `*_synthetic.log` | hand-written in the shape of real Jenkins/pytest/ruff/kubelet output |
| `real_aborted_pod_pending.log` | real: `router/feat%2Fcv-parity #1`, agent pod stuck ContainerCreating (Secret `ghcr-creds` missing, before it was made optional), aborted |
| `real_invalid_pod_spec.log` | real: drill 11 first attempt, Kaniko memory request 256Mi > limit 150Mi, pod rejected by the API server |
| `real_test_drill01.log` | real: drill 01 branch build (failing unit test; later stages skipped) |
| `real_lint_drill02.log` | real: drill 02 branch build (ruff F401/I001) |
| `real_oom_drill11.log` | real: drill 11, Kaniko OOMKilled |
