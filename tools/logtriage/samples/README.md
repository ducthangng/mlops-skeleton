# Sample console logs for tests/unit/test_logtriage.py

| file | origin |
|---|---|
| `unknown_git_not_found.log` | real: Jenkins build `router/feat%2Fcv-parity #2` (`git` missing in the python image) |
| `*_synthetic.log` | hand-written in the shape of real Jenkins/pytest/ruff/kubelet output |
| `real_aborted_pod_pending.log` | real: `router/feat%2Fcv-parity #1`, agent pod stuck ContainerCreating (Secret `ghcr-creds` missing, before it was made optional), aborted |
| `real_*.log` | real: captured from the drills in `drills/` (see NOTES.md) |
