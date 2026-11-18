SHELL := /usr/bin/env bash
CTX   ?= kind-mlops
GITOPS_DIR ?= ../mlops-gitops
K := kubectl --context $(CTX)

# ---------- platform ----------
.PHONY: up down status passwords retire-gw promote

up:            ## idempotent bring-up (cluster, ArgoCD, Jenkins, secrets, JCasC, jobs, apps)
	scripts/bootstrap.sh

down:          ## delete the kind cluster (asks for CONFIRM=1)
	@[[ "$(CONFIRM)" == 1 ]] || { echo "This deletes the kind cluster 'mlops' (Jenkins history, ArgoCD state). Re-run with CONFIRM=1"; exit 1; }
	kind delete cluster --name mlops

status:        ## what is running and is it healthy
	@$(K) get nodes
	@echo; $(K) -n argocd get applications 2>/dev/null || true
	@echo; for ns in router-dev router-prod jenkins; do echo "--- $$ns"; $(K) -n $$ns get pods 2>/dev/null; done
	@echo; helm --kube-context $(CTX) list -A

passwords:     ## admin passwords for ArgoCD and Jenkins
	@printf 'ArgoCD  admin / '; $(K) -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
	@printf 'Jenkins admin / '; $(K) -n jenkins get secret jenkins -o jsonpath='{.data.jenkins-admin-password}' | base64 -d; echo

retire-gw:     ## delete the legacy ArgoCD app 'gw' (ns default) and the resources it created
	$(K) -n argocd patch application gw --type merge -p '{"metadata":{"finalizers":["resources-finalizer.argocd.argoproj.io"]}}'
	$(K) -n argocd delete application gw --wait=true

promote:       ## copy the dev image tag to prod as a Git commit in the GitOps repo (review, then push)
	@tag=$$(sed -n 's/^  tag: "\(.*\)"/\1/p' $(GITOPS_DIR)/charts/router/values-dev.yaml); \
	 [[ -n "$$tag" ]] || { echo "no tag in values-dev.yaml"; exit 1; }; \
	 sed -i.bak "s|^  tag: .*|  tag: \"$$tag\"|" $(GITOPS_DIR)/charts/router/values-prod.yaml && rm $(GITOPS_DIR)/charts/router/values-prod.yaml.bak; \
	 cd $(GITOPS_DIR) && git commit -m "promote: router $$tag to prod" charts/router/values-prod.yaml && \
	 echo "committed. Review with 'git -C $(GITOPS_DIR) show', then push. Rollback = git revert."

# ---------- app ----------
.PHONY: install test test-integration lint cov eval redis-up

install:
	uv sync

test:          ## unit + api tests (no Redis needed)
	uv run pytest -q tests/unit tests/api

redis-up:      ## local Redis 8 for integration tests / benchmarks
	@docker ps --format '{{.Names}}' | grep -qx gw-redis || docker run -d --rm --name gw-redis -p 6379:6379 redis:8.10.3-alpine

test-integration: redis-up
	REDIS_URL=redis://localhost:6379/0 uv run pytest -q -m integration tests/integration

lint:
	uv run ruff check .

cov: redis-up  ## all tests with the CI coverage gate
	rm -f .coverage
	uv run pytest -q tests/unit tests/api --cov --cov-report=
	REDIS_URL=redis://localhost:6379/0 uv run pytest -q -m integration tests/integration --cov --cov-append --cov-report=
	uv run coverage report --fail-under=80

eval:          ## routing accuracy on dev + held-out test, gate vs evals/baseline.json
	uv run python -m evals.run --split dev --split test --gate evals/baseline.json --out reports/eval.json

# ---------- AI gateway UI prototype (gateway/ + web/) ----------
.PHONY: gw-db gw-dev gw-web gw-test gw-build

gw-db:
	cd gateway && docker compose up -d postgres

gw-dev:
	cd gateway && uv run uvicorn gateway.main:app --port 8090 --reload

gw-web:
	cd web && npm run dev

gw-test:
	cd gateway && uv run pytest -q && uv run ruff check gateway tests examples
	cd web && npm run typecheck

gw-build:
	cd web && npm run build
