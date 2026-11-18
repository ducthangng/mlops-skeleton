SHELL := /usr/bin/env bash
CTX   ?= kind-mlops
GITOPS_DIR ?= ../mlops-gitops
APP_DIR ?= ../custom-ai-gateway
K := kubectl --context $(CTX)

# ---------- platform ----------
.PHONY: up down status passwords retire-router promote llm-key ui jenkins app argocd jenkins-clean

up:            ## idempotent bring-up (cluster, ArgoCD, Jenkins, secrets, JCasC, jobs, apps)
	scripts/bootstrap.sh

down:          ## delete the kind cluster (asks for CONFIRM=1)
	@[[ "$(CONFIRM)" == 1 ]] || { echo "This deletes the kind cluster 'mlops' (Jenkins history, ArgoCD state). Re-run with CONFIRM=1"; exit 1; }
	kind delete cluster --name mlops

status:        ## what is running and is it healthy
	@$(K) get nodes
	@echo; $(K) -n argocd get applications 2>/dev/null || true
	@echo; for ns in gateway-dev gateway-prod jenkins; do echo "--- $$ns"; $(K) -n $$ns get pods 2>/dev/null; done
	@echo; helm --kube-context $(CTX) list -A

passwords:     ## admin passwords for ArgoCD and Jenkins
	@printf 'ArgoCD  admin / '; $(K) -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
	@printf 'Jenkins admin / '; $(K) -n jenkins get secret jenkins -o jsonpath='{.data.jenkins-admin-password}' | base64 -d; echo

llm-key:       ## provider keys from .env -> Secret llm-providers (dev+prod), restart the gateways
	scripts/bootstrap.sh llm_keys
	@for ns in gateway-dev gateway-prod; do $(K) -n $$ns rollout restart deploy/gw-gateway 2>/dev/null || true; done

ui:            ## port-forward Jenkins :8080, ArgoCD :8443, gateway dev :8090 (Ctrl-C stops all)
	@$(MAKE) -s passwords; echo "Jenkins http://localhost:8080  ArgoCD https://localhost:8443  gateway http://localhost:8090"
	@trap 'kill 0' INT TERM; \
	 $(K) -n jenkins port-forward svc/jenkins 8080:8080 >/dev/null & \
	 $(K) -n argocd port-forward svc/argocd-server 8443:443 >/dev/null & \
	 $(K) -n gateway-dev port-forward svc/gw-gateway 8090:80 >/dev/null & wait

jenkins:       ## open Jenkins at http://localhost:8080 (port-forward; Ctrl-C stops it)
	@printf 'Jenkins admin / '; $(K) -n jenkins get secret jenkins -o jsonpath='{.data.jenkins-admin-password}' | base64 -d; echo
	@if lsof -nP -iTCP:8080 -sTCP:LISTEN >/dev/null 2>&1; then \
	   echo "port 8080 already forwarded"; open http://localhost:8080/job/ai-gateway/; \
	 else \
	   (sleep 2; open http://localhost:8080/job/ai-gateway/) & \
	   $(K) -n jenkins port-forward svc/jenkins 8080:8080; \
	 fi

argocd:        ## open the ArgoCD UI at https://localhost:8443 (self-signed cert; Ctrl-C stops it)
	@printf 'ArgoCD admin / '; $(K) -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
	@if lsof -nP -iTCP:8443 -sTCP:LISTEN >/dev/null 2>&1; then \
	   echo "port 8443 already forwarded"; open https://localhost:8443/applications; \
	 else \
	   (sleep 2; open https://localhost:8443/applications) & \
	   $(K) -n argocd port-forward svc/argocd-server 8443:443; \
	 fi

jenkins-clean: ## delete Jenkins build history (dry run; CONFIRM=1 to delete; JOB=ai-gateway for one job/folder)
	@CONFIRM=$(CONFIRM) JOB=$(JOB) scripts/jenkins-clean.sh

APP_ENV ?= dev
APP_PORT ?= 8090
app:           ## open the AI gateway UI (ns gateway-$(APP_ENV)) at http://localhost:$(APP_PORT); APP_ENV=prod APP_PORT=8091 for prod
	@$(K) -n gateway-$(APP_ENV) rollout status deploy/gw-gateway --timeout=60s
	@if lsof -nP -iTCP:$(APP_PORT) -sTCP:LISTEN >/dev/null 2>&1; then \
	   echo "port $(APP_PORT) already in use (forward running?)"; open http://localhost:$(APP_PORT)/; \
	 else \
	   (sleep 2; open http://localhost:$(APP_PORT)/) & \
	   $(K) -n gateway-$(APP_ENV) port-forward svc/gw-gateway $(APP_PORT):80; \
	 fi

retire-router: ## delete the namespaces of the retired router service (router-dev, router-prod); CONFIRM=1
	@[[ "$(CONFIRM)" == 1 ]] || { echo "Deletes namespaces router-dev and router-prod (old router service, its secrets). Re-run with CONFIRM=1"; exit 1; }
	$(K) delete namespace router-dev router-prod --ignore-not-found

promote:       ## copy the dev image tag to prod as a Git commit in the GitOps repo (review, then push)
	@tag=$$(sed -n 's/^  tag: "\(.*\)"/\1/p' $(GITOPS_DIR)/charts/ai-gateway/values-dev.yaml); \
	 [[ -n "$$tag" && "$$tag" != none-yet ]] || { echo "no CI-built tag in values-dev.yaml yet"; exit 1; }; \
	 sed -i.bak "s|^  tag: .*|  tag: \"$$tag\"|" $(GITOPS_DIR)/charts/ai-gateway/values-prod.yaml && rm $(GITOPS_DIR)/charts/ai-gateway/values-prod.yaml.bak; \
	 cd $(GITOPS_DIR) && git commit -m "promote: ai-gateway $$tag to prod" charts/ai-gateway/values-prod.yaml && \
	 echo "committed. Review with 'git -C $(GITOPS_DIR) show', then push. Rollback = git revert."

# ---------- this repo (platform tooling) ----------
.PHONY: install test lint

install:
	uv sync

test:          ## platform tests (logtriage)
	uv run pytest -q

lint:
	uv run ruff check .

# ---------- product checks, run in the product repo ($(APP_DIR)/gateway) ----------
.PHONY: redis-up gw-test gw-eval gw-qualify

redis-up:      ## local Redis 8 on :6390 (not 6379: a host Redis there would shadow Docker's port)
	@docker ps --format '{{.Names}}' | grep -qx gw-redis-local || docker run -d --rm --name gw-redis-local -p 127.0.0.1:6390:6379 redis:8.10.3-alpine --save ""

gw-test: redis-up  ## product: lint, unit + integration tests, coverage gate (same as CI)
	cd $(APP_DIR)/gateway && uv run ruff check . && \
	 uv run pytest -q --cov=gateway --cov-report= && \
	 GATEWAY_REDIS_URL=redis://localhost:6390/0 uv run pytest -q -m integration --cov=gateway --cov-append --cov-report= && \
	 uv run coverage report --fail-under=80 | tail -1

gw-eval:       ## product: JJ routing accuracy on dev + held-out test, gate vs evals/baseline.json
	cd $(APP_DIR)/gateway && uv run python -m evals.run --split dev --split test --gate evals/baseline.json

SCENARIO ?= clean
gw-qualify: redis-up  ## product: release qualification (SCENARIO=clean|bad|partial), diff vs golden
	cd $(APP_DIR)/gateway && GATEWAY_REDIS_URL=redis://localhost:6390/0 \
	 uv run python -m qualify.run --all --scenario $(SCENARIO) --out reports/qualify --cache-dir .qualify-cache; \
	 uv run python -m qualify.diff --run reports/qualify --no-fail

# ---------- drills (drills/NN-*.sh): each prints what breaks, what to observe, how to restore ----------
.PHONY: drills
drills:        ## list drills
	@scripts/drill.sh list

drill-%-fix:   # must stay above drill-%: make 3.81 (macOS) takes the first matching pattern
	@scripts/drill.sh $* fix

drill-%:
	@scripts/drill.sh $* break
