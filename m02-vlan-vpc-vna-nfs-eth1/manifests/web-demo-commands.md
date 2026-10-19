# 部署服務 demo（kubectl 指令）

```bash
# 0. 登入 VKS 叢集（Supervisor API VIP 與叢集名稱依實際環境）
kubectl vsphere login --server=<SUPERVISOR_VIP> -u administrator@vsphere.local --insecure-skip-tls-verify \
  --tanzu-kubernetes-cluster-namespace <NAMESPACE> --tanzu-kubernetes-cluster-name <CLUSTER>
kubectl config use-context <CLUSTER>

# 1. 部署（Namespace + PVC + 首頁 Job + Deployment ×3 + LoadBalancer）
kubectl apply -f web-demo.yaml
kubectl -n web-demo wait --for=condition=complete job/seed-content --timeout=180s
kubectl -n web-demo rollout status deploy/web

# 2. 看結果
kubectl -n web-demo get pvc,pods -o wide
kubectl -n web-demo get svc web-lb          # EXTERNAL-IP = LoadBalancer VIP
VIP=$(kubectl -n web-demo get svc web-lb -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
curl -s http://$VIP/

# 3. 改內容：寫一次 PV，三個 Pod 同時生效（RWX）
POD=$(kubectl -n web-demo get pod -l app=web -o jsonpath='{.items[0].metadata.name}')
kubectl -n web-demo run edit --rm -i --restart=Never --image=harbor01.home.lab/vks-apps/busybox:1.37.0 \
  --overrides='{"spec":{"securityContext":{"runAsNonRoot":true,"runAsUser":1000,"seccompProfile":{"type":"RuntimeDefault"}},"containers":[{"name":"edit","image":"harbor01.home.lab/vks-apps/busybox:1.37.0","command":["sh","-c","echo \"<h1>web-demo v2</h1>\" > /content/index.html"],"securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}},"volumeMounts":[{"name":"c","mountPath":"/content"}]}],"volumes":[{"name":"c","persistentVolumeClaim":{"claimName":"web-content"}}]}}'
for i in 1 2 3 4 5 6; do curl -s http://$VIP/; done

# 4. 擴縮與滾動更新
kubectl -n web-demo scale deploy/web --replicas=5
kubectl -n web-demo rollout restart deploy/web && kubectl -n web-demo rollout status deploy/web
kubectl -n web-demo rollout history deploy/web

# 5. 清掉
kubectl delete ns web-demo
```
