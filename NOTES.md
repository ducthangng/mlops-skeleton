# NOTES: CV parity work (branch `feat/cv-parity` in both repos)

Machine: Apple Silicon laptop, Docker Desktop 28.5 (11.7 GiB, 12 CPUs), kind cluster `mlops` (k8s 1.37,
1 control-plane + 2 workers), ArgoCD chart 10.18.0, Jenkins chart 5.12.0 (2.580.2). Nothing was pushed.

**How it was verified without pushing:** a read-only `git daemon` on 127.0.0.1:9418 served the two local
repos (whitelisted) to the cluster as `git://host.docker.internal/<repo>`. Jenkins jobs and ArgoCD apps
were pointed there with `APP_REPO_URL` / `GITOPS_REPO_URL` (both default to GitHub). The image was built
locally and loaded into kind (`kind load docker-image`), on a throwaway local GitOps branch `verify/local`
whose tag points at that image. GHCR push + GitOps bump on `main` were NOT executed (they need your PAT and
a pushed `main`).

Order: the "AI = black box" refactor came first, because every item depends on it. B4 was committed before B1,
because the per-environment Helm values need the mock LLM and the new env vars. Everything else is in order.

---

## R (2026-11-18): routing becomes a module of the product
Decision (yours): the router is a module of the AI gateway product, not a separate service. Nothing committed.

- **Product (custom-ai-gateway), new:** `gateway/gateway/routing/` (RuleClassifier, routes loader),
  plugin `gateway/plugins/rules_router.py` (JJ: route -> first candidate model whose provider has a key, else
  the user's choice), plugin `gateway/plugins/redis_cache.py` (Redis, no retries, fail-open via the gateway),
  `routes.yaml` (same 77 rules; targets are now `provider/model` from `catalog.yaml`), `evals/` (same dev/test
  split, new baseline), `qualify/` (5 stages rewritten for `/api/chat` SSE, Postgres/SQLite, Redis cache;
  `qualify.diff`), `tests/test_{routing,redis_cache,qualify}.py`, root `Jenkinsfile`, root `.dockerignore`.
  Changed: `Dockerfile` (+ routes.yaml), `mock_provider.py` (delay from `GATEWAY_MOCK_DELAY_S`),
  `pyproject.toml` (+ redis; dev: fakeredis, pytest-cov; pytest marker `integration`), `.gitignore`.
- **GitOps:** new chart `charts/ai-gateway` (gateway + Postgres StatefulSet + Redis; `ghcr-pull`,
  `postgres-auth`, `llm-providers` Secrets referenced, never stored), `charts/router` deleted, apps point at
  `charts/ai-gateway` in ns `gateway-dev` / `gateway-prod`, job `router.xml` -> `ai-gateway.xml`, qualify job
  script path `gateway/qualify/Jenkinsfile`.
- **Platform (this repo):** removed `app/ mock_llm/ evals/ qualify/ routes.yaml Dockerfile Jenkinsfile
  tests/{api,integration} scripts/bench.sh tools/logtriage/qualify_diff.py`; results moved to
  `docs/archive/router-service/`. bootstrap: `postgres-auth` (random, once), `llm-providers` from
  ANTHROPIC/OPENAI/GEMINI keys in `.env` (old `UPSTREAM_API_KEY` still read as the OpenAI key). Makefile:
  `app` (UI on :8090), `gw-test`, `gw-eval`, `gw-qualify`, `retire-router`. Drills rewritten for the product;
  08 rate limit, 09 mock-llm faults, 13 router OOM, 15 semantic false hit replaced by 08 Postgres down,
  09 invalid provider key, 13 gateway OOM, 15 truncated answer not cached.
- **Measured:** image 313 MB, `docker build --no-cache` 12 s; 37 unit/API + 1 integration test, coverage 84%
  of `gateway`; JJ dev 100% (52), test 93.75% (48); qualify bad 0.19 s / partial 1.3 s / clean 36 s / cached 0.07 s;
  load 608 chats at 20/s offered, p95 17.9 ms, 0 errors (SQLite, mock); cache stage 100% repeat hits, 100% ok
  with Redis down. Chart installed in kind with the local image: JJ routed, miss -> hit, UI 200.
- **Drills verified:** 01-03 (on a copy of the uncommitted tree), 08, 09, 12, 13, 14, 15, 16.
  **Not re-run:** 04, 05, 06, 07, 10, 11 (need GHCR image / pushed repos).
- **Bugs found:** postgres-auth password ended with `\n` (`openssl rand` + `--from-file`) -> gateway could not
  log in (drill 08 on a fresh namespace); a process substitution reused for 2 namespaces would have made an
  empty Secret; a pod does not see a changed `envFrom` Secret until it restarts (drill 09).
- **Dropped with the router service** (no equivalent in the product yet): per-client rate limiting, semantic
  cache, Prometheus `/metrics`, MiniLM option, replay benchmark. See the CV table below.

## B0: black-box AI, image without PyTorch
- Built: `app/classifier.py` (`Classifier.classify`, default `RuleClassifier`), `app/embedder.py`
  (`Embedder.embed`, default `HashingEmbedder`), `app/minilm.py` (optional extra `minilm`, lazy import,
  `CLASSIFIER=minilm` / `EMBEDDER=minilm`), `app/config.py` (routes.yaml read once: hash = config version).
  Eval set split by sha256 into `evals/dev.jsonl` (52; the rules were written against it) and `evals/test.jsonl` (48, held out).
- Verify: `docker build -t router:after . && docker image inspect router:after --format '{{.Size}}'`;
  `make eval`. The Dockerfile fails the build if `import torch` works.
- Numbers: image 1,447,666,294 B -> 249,839,278 B; `docker build --no-cache` 50 s -> 7 s; held-out
  routing accuracy 93.75% (45/48).
  Kaniko in kind, `--no-push`, context streamed as a tarball (`kubectl run -i ... --context tar://stdin`):
  old image at a 4 Gi limit: 184 s with `--snapshot-mode=redo --compressed-caching=false` and 128 s with
  default flags, **no OOM in either**. New image: 18 s at a 1 Gi limit (Jenkins build.json). The OOMKilled
  you reported came from push + `--cache=true`, which I could not re-run without GHCR credentials,
  so the README says "most likely during compress/upload" and does not claim a measured OOM.
- Honesty note: I read the whole eval file once while splitting it, before writing the rules. I wrote the
  rules from the utterances and the dev split, and did not tune them against test after measuring it.

## B1: bootstrap, per-env values, one ArgoCD app per env
- Built: `scripts/bootstrap.sh` + `scripts/lib.sh`; `make up | down (CONFIRM=1) | status | passwords |
  retire-gw | promote`. GitOps repo: `values.yaml` (shared), `values-dev.yaml` (CI bumps it),
  `values-prod.yaml` (changed only by promotion), templates for router, redis (8.10.3), mock-llm;
  `argocd/gw-dev.yaml` -> ns `router-dev`, `argocd/gw-prod.yaml` -> ns `router-prod` (releaseName `gw`).
- Verify: `scripts/bootstrap.sh` twice -> every step prints `skip` for what exists; `make status`.
  In-cluster smoke: `kubectl -n router-dev run c --rm -i --image=curlimages/curl -- curl -s http://gw-router/healthz`.
- Verified: idempotent run on the existing cluster (cluster, ArgoCD, Jenkins skipped); gw-dev 1 replica,
  gw-prod 2 replicas, both Synced/Healthy from Git; in-cluster: miss -> exact hit, burst 10x200 then 429.
- Not done: `argocd/gw.yaml` was removed from Git, but the live app `gw` (ns default) was left untouched.
  Retire it yourself with `make retire-gw` (cascading delete) before or when you push the GitOps branch.
  Otherwise ArgoCD would sync the new chart into `default` with tag `set-per-env`.
  `make down` was not run (it deletes the cluster).

## B2: Jenkins configuration as code (no helm upgrade, no plugin changes)
- Plugins found (68): configuration-as-code, workflow-multibranch, workflow-aggregator, git, kubernetes,
  credentials(-binding), pipeline-model-definition, ... **Absent: job-dsl, junit, pipeline-utility-steps.**
- Built: `jenkins/casc/ci.yaml` (GitOps repo), applied as ConfigMap `jenkins-casc-ci` with label
  `jenkins-jenkins-config=true`; the chart's config-reload sidecar hot-reloads it. Jobs `router`
  (multibranch, branch indexing every 2 min) and `qualify` (pipeline, SCENARIO + NO_CACHE params,
  pollSCM H/2) are created or updated from committed `jenkins/jobs/*.xml` via REST. Credentials
  `github-pat` + `ghcr-creds` are synced from the k8s Secret `jenkins/github-pat`, which bootstrap creates from
  `GITHUB_USER`/`GITHUB_PAT`. All REST calls run inside the controller pod, using the admin password file
  the chart already mounts; bodies go via stdin, never argv.
- Verify: `scripts/jenkins-configure.sh` (run it twice: create, then update);
  `uv run python -m tools.logtriage jenkins --job router --branch main`.
- Verified: JCasC reload observed (system message); numExecutors 0; create -> update path for jobs and
  credentials (tested with a dummy Secret whose password contained `&<>`, then deleted).
- Deviations, and why:
  - `numExecutors: 0` is already set by the chart's own JCasC file. Repeating it in a second file
    is a JCasC merge conflict, so it is verified (API: `"numExecutors":0`), not re-declared.
  - Credentials are not in JCasC: JCasC can only resolve secrets mounted into the controller, and adding a
    mount changes the StatefulSet (= helm upgrade). Sync via REST from the k8s Secret is the simplest
    working alternative.
  - `pollSCM H/2` for `router` is the multibranch equivalent (periodic branch indexing, 2 min), because
    `when { branch 'main' }` only works in multibranch jobs (BRANCH_NAME).
- Incident: after Docker Desktop was restarted (it was stopped when I started), `jenkins-0` crash-looped in
  `init`: the chart runs `yes n | cp -i` under `set -e`, coreutils >= 9.2 returns 1 when `cp -i` skips a file,
  and the recreated sandbox kept the old emptyDir. Fix: delete the pod (jenkins_home is a PVC, the plugin
  list is unchanged). bootstrap now detects and heals this.

## B3: tests + Jenkinsfile v2
- Built: `tests/unit` (classifier, threshold/fallback, cache keys, embedder, upstream retry/fallback,
  logtriage, qualify), `tests/api` (TestClient + fakeredis + in-process mock-llm: hit/miss, semantic,
  fail-open, 429 + Retry-After, per-client override, fail-closed 503, fallback not cached, 502, 422/400),
  `tests/integration` (marker `integration`, real Redis: atomic token bucket under 50 concurrent calls,
  refill + TTL, exact TTL, both semantic backends, bounded list). Jenkinsfile: timeout, buildDiscarder,
  disableConcurrentBuilds, parallel {lint | unit+api}, integration (Redis sidecar), coverage >= 80%,
  eval gate (baseline - 2 points), Kaniko build (`--no-push` off main), bump only `when { branch 'main' }`,
  post: archiveArtifacts (JUnit XML, coverage.xml, eval.json, build.json).
- Verify: `make test`, `make test-integration`, `make cov`, `make eval`; Jenkins `router/<branch>`.
- Numbers: 79 unit+api + 6 integration tests, combined coverage 95% (gate 80%). Branch pipeline 34–57 s
  (builds #3–#7). Kaniko 18 s with a 1 Gi limit (was OOMKilled with PyTorch).
- Skipped: the `junit` post step (plugin not installed; installing plugins was out of scope). The XML is archived.
- Not verified: the `main` path (GHCR push + GitOps bump) needs your PAT and a pushed main.

## B4: gateway features
- Built: per-client token bucket (Lua, `redis TIME`, X-API-Key, 429 + Retry-After, fail-open/closed),
  `/metrics` (latency {route,cache}, cache_hits_total{type}, rate_limited_total, upstream_calls_total,
  upstream_errors_total, + misses, cache errors, fallbacks), `POST /v1/chat/completions` (classify ->
  caches -> upstream with timeout, jittered exponential backoff on 429/5xx/timeout, fallback model),
  `mock_llm/` (latency, jitter, error rate/status/models, token counts) shipped in the chart.
- Verify: `make test`; drills 06, 08, 09.
- Incident: redis-py's default retry/backoff made fail-open take seconds per request with Redis down
  (API tests 74 s). Client built with `Retry(NoBackoff(), 0)`: 0.3 s.

## B5: tools/logtriage
- Built: `analyze <logs>` and `jenkins --job --branch --last`: ordered regex rules (oom, image_pull, auth,
  timeout, lint, test, unknown), evidence line + line number, secondary matches in `also`; slow builds =
  duration > mean + 3 sigma of the *other* finished builds (leave-one-out); Markdown + JSON.
- Verify: `uv run python -m tools.logtriage analyze tools/logtriage/samples/*.log`;
  `JENKINS_PASSWORD=... uv run python -m tools.logtriage jenkins --job router --branch feat/cv-parity`.
- Samples: 7 real Jenkins logs (from this work and the drills) + 6 synthetic ones (labelled in samples/README.md).
- Real-data fixes: unknown -> first explicit `ERROR:` line (root cause), wrapper lines skipped; qualify
  stage failures -> `test`; a clear message on 401/403. In the real history the tool flagged build #1 as
  slow (105 s > 80 s threshold). That build was the one stuck in ContainerCreating.

## B6: semantic cache measured
- Built: `evals/semantic_pairs.jsonl` (36 paraphrase + 36 near-miss pairs, typed), `evals/semantic_eval.py`
  (tau table; tau = lowest value with false-hit rate <= 0), `evals/bench.py` + `scripts/bench.sh`
  (no-cache vs exact vs exact+semantic on the same seeded workload).
- Verify: `make semantic-eval`, `make bench` (needs ports 8000/8001 free and Docker).
- Numbers (setup in README / docs/results): tau 0.95 -> 27.8% paraphrase hits, 0% false hits; 300 requests
  (149 unique / 88 repeat / 63 paraphrase): LLM calls 300 / 164 / 161 -> 0 / 45.3 / 46.3% avoided;
  exact hit p50 5.4 ms, semantic hit p50 7.0 ms, miss p50 245 ms (mock 200 ms + U(0,50) ms).
- Backend: Redis 8.10.3 ships `search` -> RediSearch KNN used in cluster/bench (logged
  `semantic cache backend: redisearch`); numpy fallback covered by the API tests (fakeredis) and an integration test.
- Incident: a host Redis (`/usr/local/opt/redis`, 7.2.1, on 127.0.0.1:6379) shadowed Docker's `-p 6379`,
  and my first benchmark wrote 164 `exact:*` keys into it. I deleted exactly those keys (DBSIZE was 164,
  all mine), and local Redis for tests/bench moved to :6390.

## B7: qualify (golden regression)
- Built: `qualify/run.py`, `stages.py` (config_lint, contract, routing, cache, load), `compare.py`,
  `golden/`, `scenarios/{bad,partial}`, `Jenkinsfile`; `logtriage qualify-diff`; PVC `qualify-cache`.
- Verify: `REDIS_URL=redis://localhost:6390/0 uv run python -m qualify.run --all --scenario partial
  --cache-dir .drills/qualify-cache`; `make drill-16`; Jenkins job `qualify` with SCENARIO.
- Verified in Jenkins: clean passes; a rerun skips every stage (PVC cache); bad fails at config_lint in 14 s
  (4 stages not run); partial reuses contract from the cache and fails routing with 6 evidence lines
  (accuracy 0.97 -> 0.91).
- Fixes found on real runs: the first Jenkins load run had p95 84.7 ms (cold start) vs 19.2 ms on the next
  run -> a 3 s warm-up is excluded, and later uncached runs gave p95 4.9 / 4.4 ms. Stage code was not
  part of the cache key (a change to the load code would have been skipped) -> now it is. Golden was
  recorded on the laptop (p95 12.7 ms) with tolerance 2x + 25 ms.

## B8: drills
- `make drills` lists 16; `make drill-NN` / `make drill-NN-fix`.
- Verified end to end here: 01 + 02 (built in Jenkins from the drill branches: red, later stages skipped,
  logtriage test/lint), 03 (gate: 66.7% vs floor 91.8%), 04 (ArgoCD: ErrImagePull, old pod serves,
  `git revert` rolls back), 05 (selfHeal <= 10 s), 06, 07, 08, 09, 11 (Kaniko OOMKilled at 150Mi),
  12, 13 (OOMKilled 137, old pod serves), 14, 15 (false hit in-cluster, cos 0.9062), 16 (local + Jenkins).
- Partly verified: 10. With no ghcr-creds Secret, branch builds pass (optional volume, builds #3–#9);
  the main-branch push failure (auth) needs your main + GHCR.
- Bugs found by the drills: a Kaniko request (256Mi) above the drill limit made the API server reject the
  pod -> request 128Mi; `kubectl run -i` lost the output of fast containers -> `sleep 1`; helm 4
  deprecation of `--wait=false` -> `--wait=hookOnly`.

## B9: README
- Rewritten: architecture, request path and release flow (mermaid), Results with measured numbers only
  (+ setup), runbook table mapped to drills, 20 lessons, honest roadmap. The old Results table (MiniLM
  accuracy, old latencies, 1.4 GB) was removed: it described the previous system and was not re-measured.
  `k8s/` (raw manifests for `router:0.3`, superseded by the chart) was removed.
- Still TODO with exact command: time from `git push` to a new pod running.

---

## "Failed twice -> simplest alternative" log
| problem | alternative taken |
|---|---|
| JCasC credentials from k8s Secrets (needs a StatefulSet mount = helm upgrade) | REST sync from the Secret (scripts/jenkins-configure.sh) |
| Job DSL (plugin absent) | committed config.xml + REST create/update |
| `junit` step (plugin absent) | archive the JUnit XML |

## CV bullet -> proof (after the 2026-11-18 restructure)

Status: **holds** = true for the current product + platform; **partly** = true with the caveat given;
**retired** = was true only for the old router service (code in git history, numbers in
`docs/archive/router-service/`); rewrite or drop the bullet before using it.

| CV bullet | status | file / command that proves it |
|---|---|---|
| 1. GitOps delivery; CI never needs cluster access; rollback = git revert | holds (full loop for the new image not run yet) | product `Jenkinsfile` (no kubectl; output = GHCR image + commit to `charts/ai-gateway/values-dev.yaml`), `mlops-router-gitops/argocd/gw-*.yaml`, `make drill-04` / `-fix`, `make drill-05` |
| 2. pytest unit + integration in Jenkins on every commit; failing build never reaches the cluster | holds | product `gateway/tests/`, `Jenkinsfile` stages (coverage gate 80%, measured 84%), `make drill-01`, `make gw-test` |
| 3. Prometheus metrics: latency, cache hit rate, rate-limit events | **retired** | the product has no `/metrics`; it records latency, cache status and routing per request in Postgres (`GET /api/requests`, `/api/stats`). Roadmap item |
| 4. FastAPI + Redis (+ Postgres) as Helm charts, values per env; cluster setup scripted in Bash | holds | `mlops-router-gitops/charts/ai-gateway/values-{dev,prod}.yaml`, `scripts/bootstrap.sh` (`make up`) |
| 5. Classifies each prompt, forwards to a suitable model/config | holds | product `gateway/routing/`, `gateway/plugins/rules_router.py`, `routes.yaml`, `make gw-eval` (held-out 93.75%), qualify routing stage |
| 5b. … per-client rate limits | **retired** | not in the product |
| 6. Semantic cache on local embeddings + vector search, measured | **retired** | the product has an exact Redis cache (`redis_cache.py`, qualify cache stage: 100% repeat hits, fail-open). Semantic numbers: `docs/archive/router-service/` |
| 7. Release qualification: golden regression, fail fast, only new failures reported, cached stages | holds | product `gateway/qualify/`, `make gw-qualify SCENARIO=bad|partial`, `make drill-16` |

## State left on this machine (2026-11-18)
- Cluster: gw-dev / gw-prod still run the old router chart from GitHub `main` in ns router-dev / router-prod
  (nothing pushed yet). After the GitOps change is pushed, ArgoCD moves them to `charts/ai-gateway` in
  gateway-dev / gateway-prod; router-dev / router-prod are then empty: `make retire-router CONFIRM=1`.
- Jenkins: job `router` still exists until `make up` (then `ai-gateway` is created; delete `router` in the UI).
- The image `ai-gateway:local` is loaded in the kind nodes (used to verify the chart and drills); ns `drills` deleted.
- Local Docker container `gw-redis-local` (Redis 8 on 127.0.0.1:6390) for `make gw-test` / `gw-qualify`.
