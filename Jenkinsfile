// Gateway CI. Runs on every branch (multibranch job `router`); only `main` publishes.
// CI never talks to the cluster: its only output is an image in GHCR and a Git commit in the
// GitOps repo. ArgoCD does the deploy. Rollback = `git revert` in the GitOps repo.
//
// Plugins: no `junit` (not installed, and we don't change plugins) -> JUnit XML is archived.

def podYaml(String kanikoMemory) {
  return """
apiVersion: v1
kind: Pod
spec:
  containers:
    - name: python
      image: python:3.12-slim
      # PID 1 must handle SIGTERM, otherwise the pod always waits the full grace period (lesson 6)
      command: ["sh", "-c", "trap 'exit 0' TERM; sleep infinity & wait"]
      resources:
        requests: { cpu: 500m, memory: 512Mi }
        limits:   { memory: 1536Mi }
    - name: redis                      # real Redis for tests/integration (marker: integration)
      image: redis:8.10.3-alpine
      args: ["--save", "", "--maxmemory", "64mb"]
      resources:
        requests: { cpu: 50m, memory: 32Mi }
        limits:   { memory: 128Mi }
    - name: kaniko
      image: martizih/kaniko:v1.26.6-debug    # maintained fork; Google archived Kaniko (lesson 11)
      command: ["/busybox/cat"]
      tty: true
      resources:
        requests: { cpu: 500m, memory: 128Mi }    # must be <= the limit, or the API server rejects the pod
        limits:   { memory: ${kanikoMemory} }
      volumeMounts:
        - { name: docker-config, mountPath: /kaniko/.docker }
  volumes:
    - name: docker-config
      secret:
        secretName: ghcr-creds
        optional: true                 # branch builds don't push, so they don't need it
        items: [{ key: .dockerconfigjson, path: config.json }]
"""
}

pipeline {
  agent {
    kubernetes {
      yaml podYaml(params.KANIKO_MEMORY ?: '1Gi')
      defaultContainer 'python'
    }
  }
  parameters {
    string(name: 'KANIKO_MEMORY', defaultValue: '1Gi',
           description: 'Kaniko memory limit. Drill: 150Mi -> OOMKilled (exit 137).')
  }
  options {
    timeout(time: 30, unit: 'MINUTES')
    buildDiscarder(logRotator(numToKeepStr: '20', artifactNumToKeepStr: '10'))
    disableConcurrentBuilds()
  }
  environment {
    REGISTRY    = 'ghcr.io/ducthangng'
    GITOPS_REPO = 'github.com/ducthangng/mlops-router-gitops.git'
    UV_LINK_MODE = 'copy'
  }
  stages {
    stage('Setup') {
      steps {
        sh 'pip install --quiet uv==0.11.28 && uv sync --frozen && mkdir -p reports'
      }
    }
    stage('Checks') {
      parallel {
        stage('Lint') {
          steps { sh 'uv run ruff check . --output-format=concise' }
        }
        stage('Unit + API tests') {
          steps {
            sh 'uv run pytest -q tests/unit tests/api --cov --cov-report= --junitxml=reports/junit-unit.xml'
          }
        }
      }
    }
    stage('Integration tests') {
      steps {
        sh '''
          for i in $(seq 1 20); do python -c "import socket; socket.create_connection(('localhost', 6379), 1)" && break; sleep 1; done
          REDIS_URL=redis://localhost:6379/0 uv run pytest -q -m integration tests/integration \
            --cov --cov-append --cov-report= --junitxml=reports/junit-integration.xml
        '''
      }
    }
    stage('Coverage gate (>= 80%)') {
      steps {
        sh 'uv run coverage report --fail-under=80 && uv run coverage xml -o reports/coverage.xml'
      }
    }
    stage('Routing eval gate') {
      steps {
        // held-out test split accuracy must be >= evals/baseline.json - 2 points
        sh 'uv run python -m evals.run --split test --gate evals/baseline.json --out reports/eval.json'
      }
    }
    stage('Build image') {
      steps {
        script { env.TAG = env.GIT_COMMIT.take(7) }   // set by the checkout; python image has no git
        container(name: 'kaniko', shell: '/busybox/sh') {
          sh '''#!/busybox/sh
            set -e
            if [ "$BRANCH_NAME" = "main" ]; then
              PUSH="--destination $REGISTRY/router:$TAG --cache=true --cache-repo $REGISTRY/router-cache"
            else
              PUSH="--no-push"             # branches: prove the Dockerfile builds, publish nothing
            fi
            start=$(date +%s)
            /kaniko/executor --context "$WORKSPACE" --dockerfile Dockerfile $PUSH \
              --snapshot-mode=redo --compressed-caching=false --digest-file "$WORKSPACE/reports/image.digest"
            echo "{\\"tag\\": \\"$TAG\\", \\"branch\\": \\"$BRANCH_NAME\\", \\"build_seconds\\": $(( $(date +%s) - start ))}" \
              > "$WORKSPACE/reports/build.json"
            cat "$WORKSPACE/reports/build.json"
          '''
        }
      }
    }
    stage('Bump GitOps (dev)') {
      when { branch 'main' }
      steps {
        container('jnlp') {            // the inbound-agent image has git
          withCredentials([usernamePassword(credentialsId: 'github-pat',
                           usernameVariable: 'GH_USER', passwordVariable: 'GH_TOKEN')]) {
            sh '''
              set -e
              rm -rf gitops
              git clone --depth 1 "https://$GH_USER:$GH_TOKEN@$GITOPS_REPO" gitops
              cd gitops
              sed -i "s|^  tag: .*|  tag: \\"$TAG\\"|" charts/router/values-dev.yaml
              git config user.name  "jenkins"
              git config user.email "jenkins@local"
              if git diff --quiet; then echo "dev already at $TAG"; exit 0; fi
              git commit -am "router: dev -> $TAG"
              for i in 1 2 3; do git push && exit 0; git pull --rebase; done
              exit 1
            '''
          }
        }
      }
    }
  }
  post {
    always {
      archiveArtifacts artifacts: 'reports/**', allowEmptyArchive: true
    }
  }
}
