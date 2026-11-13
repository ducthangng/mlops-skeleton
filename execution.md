kind delete cluster --name mlops
kind create cluster --config kind-config.yaml
helm install argocd argo/argo-cd -n argocd --create-namespace
kubectl apply -f ../mlops-gitops/argocd/gw.yaml
kubectl get pods -A -w


kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
kubectl -n argocd port-forward svc/argocd-server 8080:443