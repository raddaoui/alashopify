# Onboarding & Troubleshooting Guide

A practical guide for newcomers to the **SRE Agent Demo** (shop microservices on
AKS). It explains what the services are, where to find them, and how to debug
common issues. For the high-level design, see
[ARCHITECTURE.md](ARCHITECTURE.md).

---

## 1. The services at a glance

The app is **4 FastAPI microservices** plus an **in-cluster MySQL** database, all
running in the `shopdemo` namespace on AKS.

| Service | Role | K8s kind | Exposure | Container port |
|---|---|---|---|---|
| `gateway` | Public backend-for-frontend; routes/aggregates calls and serves the storefront UI | Deployment | `LoadBalancer` | 8000 |
| `users` | Customer profile lookups | Deployment | `ClusterIP` | 8000 |
| `products` | Product catalog lookups | Deployment | `ClusterIP` | 8000 |
| `orders` | Validates user + products, computes totals, writes orders | Deployment | `ClusterIP` | 8000 |
| `mysql` | Relational store for users, products, orders | StatefulSet + PVC | headless `ClusterIP` | 3306 |

Only `gateway` is reachable from outside the cluster. Internal services are
reached by Kubernetes DNS names (`http://users`, `http://products`,
`http://orders`) on port `80`, mapped to container port `8000`.

### Endpoints

**Gateway (public)** — base URL is the LoadBalancer IP:

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Storefront UI (product grid, cart, checkout) |
| GET | `/api/products` | List products |
| GET | `/api/products/{product_id}` | Single product |
| GET | `/api/users/{user_id}` | Single user |
| POST | `/api/checkout` | Place an order (fans out to orders → users/products → MySQL) |
| GET | `/api/orders/{order_id}` | Fetch an order |
| GET | `/healthz`, `/readyz` | Liveness / readiness probes |

**Internal services** expose their own routes (`/users/{id}`,
`/products`, `/products/{id}`, `/orders`, `/orders/{id}`) plus `/healthz` and
`/readyz` on every service. The data services' `/readyz` also checks DB
connectivity.

---

## 2. Where to find things

| What | Where |
|---|---|
| Service source code | `services/<name>/app/main.py` |
| Shared code (DB, telemetry, HTTP client) | `services/common/` |
| Per-service dependencies | `services/<name>/requirements.txt` |
| Dockerfiles | `services/<name>/Dockerfile` |
| Kubernetes manifests | `k8s/` (one file per service + MySQL, config, secret, namespace) |
| App config (service URLs, DB host, timeout) | `k8s/app-config.yaml` (ConfigMap `app-config`) |
| Secrets (MySQL creds, App Insights conn string) | `k8s/app-secret.yaml` (Secret `app-secret`) |
| DB schema + seed data | `k8s/mysql-initdb-configmap.yaml` and `db/` |
| CI/CD pipeline | `.github/workflows/deploy.yml` |
| Load test scripts | `scripts/loadtest.sh`, `scripts/loadtest.js` |
| Alert-rule script | `scripts/create-alerts.sh` |

### Azure resources (defaults)

| Resource | Name | Notes |
|---|---|---|
| Region | `westus3` | |
| Resource group | `ala-shopify-rg` | |
| AKS cluster | `ala-shopify-aks` | namespace `shopdemo` |
| Container registry | `alashopifyacr` | images tagged with Git SHA |
| Application Insights | `ala-shopify-ai` | distributed traces, metrics |
| Log Analytics workspace | `ala-shopify-logs` | Container Insights + App Insights backing store |

---

## 3. First-time setup for your machine

```bash
# Azure CLI + kubectl + kubelogin
az login
az aks install-cli                      # installs kubectl AND kubelogin

# Point kubectl at the demo cluster
az aks get-credentials \
  --resource-group ala-shopify-rg \
  --name ala-shopify-aks \
  --overwrite-existing

# Verify you're on the right cluster
kubectl config current-context          # should be ala-shopify-aks
kubectl get pods -n shopdemo
```

> If `kubectl` commands fail with a `kubelogin` / AAD error, run
> `az aks install-cli` (it installs `kubelogin`) and re-run `get-credentials`.

---

## 4. Everyday debugging commands

```bash
# Overall health
kubectl get pods -n shopdemo
kubectl get svc  -n shopdemo
kubectl get deploy,hpa -n shopdemo

# Describe a pod (events, probe failures, restart reasons)
kubectl describe pod -n shopdemo <pod-name>

# Logs (current + previous container after a crash)
kubectl logs -n shopdemo deploy/orders
kubectl logs -n shopdemo deploy/orders --previous
kubectl logs -n shopdemo deploy/orders -f          # follow

# Inspect env vars actually seen by a running pod
kubectl exec -n shopdemo deploy/orders -- printenv | sort

# Shell into a pod
kubectl exec -it -n shopdemo deploy/orders -- /bin/sh

# Test an internal service from inside the cluster
kubectl run curl --rm -it --image=curlimages/curl -n shopdemo -- \
  curl -s http://products/products

# Restart a deployment (re-reads ConfigMap/Secret env at startup)
kubectl rollout restart deploy/orders -n shopdemo
kubectl rollout status  deploy/orders -n shopdemo
```

---

## 5. Common issues & fixes

### Pod stuck in `Pending`
- Usually unschedulable (no node capacity). Check with
  `kubectl describe pod -n shopdemo <pod>` and look at the **Events**.

### Pod in `CrashLoopBackOff`
- Read the previous logs: `kubectl logs -n shopdemo <pod> --previous`.
- Common causes: bad config, missing dependency, DB not reachable yet.

### Readiness probe failing / pod not `Ready`
- For data services, `/readyz` checks the database. Confirm MySQL is up:
  `kubectl get pods -n shopdemo -l app=mysql`.
- Check `app-config` has the right `DB_HOST` / `DB_NAME` and `app-secret` has
  valid MySQL credentials.

### `ImagePullBackOff` / `ErrImagePull`
- The image tag (Git SHA) may not exist in ACR, or ACR isn't attached to AKS.
- Verify the tag in the deploy workflow run and that the cluster has acr pull
  access (`az aks check-acr` or re-run `--attach-acr`).

### Gateway has no external IP
- `kubectl get svc gateway -n shopdemo -w` and wait for the LoadBalancer IP to
  be assigned. It can take a couple of minutes.

### Nothing showing up in Application Insights
This is the most common observability gotcha. Work through it in order:

1. **Is the connection string set in the secret?**
   ```bash
   kubectl get secret app-secret -n shopdemo \
     -o jsonpath='{.data.APPLICATIONINSIGHTS_CONNECTION_STRING}' | base64 -d
   ```
   It should be a non-empty `InstrumentationKey=...;IngestionEndpoint=...` string
   that matches the `ala-shopify-ai` resource.
2. **Did the pods pick it up?** Env vars are read once at startup. If the secret
   was set *after* the pods started, restart them:
   ```bash
   kubectl rollout restart deploy/gateway deploy/users deploy/products deploy/orders -n shopdemo
   ```
3. **Did telemetry actually configure?** Check the startup logs:
   ```bash
   kubectl logs -n shopdemo deploy/orders | grep -i telemetry
   ```
   You want `Azure Monitor OpenTelemetry configured for orders` — **not**
   `telemetry disabled` or `Failed to configure telemetry`. Telemetry setup is
   wrapped in a try/except so a failure never crashes the app; it just silently
   stops exporting. (Historically this broke when the base image lacked
   `setuptools`/`pkg_resources` — the OpenTelemetry import needs it.)
4. **Are you looking at the right cluster/region?** Confirm
   `kubectl config current-context` is `ala-shopify-aks` and the connection
   string's `IngestionEndpoint` is `westus3`.
5. **Generate traffic and wait.** Run a load test, then allow ~2–5 minutes for
   ingestion before checking the portal.

### Useful KQL in the App Insights / Log Analytics Logs blade

```kusto
// Request volume, latency and failures by service
requests
| where timestamp > ago(1h)
| summarize count(), p95=percentile(duration, 95), failures=countif(success == false)
  by cloud_RoleName
| order by p95 desc

// Recent failed checkout requests
requests
| where timestamp > ago(1h)
| where name contains "checkout" or url endswith "/api/checkout"
| where success == false
| project timestamp, cloud_RoleName, resultCode, duration, operation_Id

// Exceptions in the last hour
exceptions
| where timestamp > ago(1h)
| summarize count() by cloud_RoleName, type, outerMessage
```

---

## 6. Generating load

```bash
GATEWAY_IP=$(kubectl get svc gateway -n shopdemo \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

# Bash/curl driver: <url> <requests> <concurrency>
./scripts/loadtest.sh http://$GATEWAY_IP 300 10

# or k6
k6 run -e GATEWAY_URL=http://$GATEWAY_IP scripts/loadtest.js
```

---

## 7. Alerts

`scripts/create-alerts.sh` provisions an action group plus three Azure Monitor
alert rules: checkout 5xx rate, checkout p95 latency, and shopdemo pod restarts.
Run it with the resource names and an email:

```bash
RESOURCE_GROUP=ala-shopify-rg \
APPINSIGHTS_NAME=ala-shopify-ai \
WORKSPACE_NAME=ala-shopify-logs \
ALERT_EMAIL=you@example.com \
  ./scripts/create-alerts.sh
```

---

## 8. Where to look when something is "slow" or "failing"

A quick triage order that mirrors how the request flows:

1. **Gateway logs** — is the public entry point erroring or timing out?
2. **Distributed traces in App Insights** — open a slow/failed checkout
   operation and walk the spans to see which downstream service or DB call is
   the bottleneck.
3. **The specific downstream service** (`orders`, `users`, `products`) logs and
   metrics for the role flagged by the trace.
4. **MySQL** — connectivity and query timing, if the slow span is a DB call.
5. **Container Insights** — pod restarts, CPU/memory pressure, throttling.

Let the telemetry point you to the failing component rather than guessing.
