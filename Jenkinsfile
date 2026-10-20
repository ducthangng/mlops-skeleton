pipeline {
  agent {
    kubernetes {
      yaml '''
apiVersion: v1
kind: Pod
spec:
  containers:
    - name: python
      image: python:3.12-slim
      command: ["sleep"]
      args: ["infinity"]
    - name: kaniko
      image: martizih/kaniko:v1.26.6-debug
      command: ["/busybox/cat"]
      tty: true
      resources:
        requests: { cpu: "1", memory: 2Gi }
        limits:   { memory: 4Gi }
      volumeMounts:
        - name: docker-config
          mountPath: /kaniko/.docker
  volumes:
    - name: docker-config
      secret:
        secretName: ghcr-creds
        items:
          - key: .dockerconfigjson
            path: config.json
'''
    }
  }
  environment {
    REGISTRY    = 'ghcr.io/ducthangng'
    GITOPS_REPO = 'github.com/ducthangng/router-gitops.git'
  }
  triggers { pollSCM('H/2 * * * *') }      // GitHub không gọi được vào laptop, nên Jenkins tự đi hỏi mỗi 2 phút
  stages {
    stage('Lint & Test') {
      steps {
        container('python') {
          sh '''
            pip install --quiet ruff pytest pyyaml
            ruff check app evals tests
            pytest -q tests
          '''
        }
      }
    }
    stage('Build & Push') {
      steps {
        script { env.TAG = sh(script: 'git rev-parse --short HEAD', returnStdout: true).trim() }
        container(name: 'kaniko', shell: '/busybox/sh') {
          sh '''#!/busybox/sh
            /kaniko/executor \
              --context "$WORKSPACE" \
              --dockerfile Dockerfile \
              --destination "$REGISTRY/router:$TAG" \
              --cache=true --cache-repo "$REGISTRY/router-cache"
          '''
        }
      }
    }
    stage('Bump GitOps tag') {
      steps {
        withCredentials([usernamePassword(credentialsId: 'github-pat',
                         usernameVariable: 'GH_USER', passwordVariable: 'GH_TOKEN')]) {
          sh '''
            rm -rf gitops
            git clone "https://$GH_USER:$GH_TOKEN@$GITOPS_REPO" gitops
            cd gitops
            sed -i "s|^  tag: .*|  tag: \\"$TAG\\"|" charts/router/values.yaml
            git config user.name  "jenkins"
            git config user.email "jenkins@local"
            git commit -am "router: $TAG"
            git push
          '''
        }
      }
    }
  }
}