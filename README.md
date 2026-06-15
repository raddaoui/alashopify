# SRE Agent Demo — Shop microservices on AKS

A small e-commerce app made of **4 FastAPI microservices** + an **in-cluster MySQL**
database, deployed to **Azure Kubernetes Service (AKS)**, with images in **Azure
Container Registry (ACR)**, **OpenTelemetry → Application Insights** tracing, and
**Azure Monitor alert rules**.

The demo's purpose: deploy a healthy `main`, then deploy a broken `release/v2`
that degrades the checkout path (high latency + intermittent HTTP 500s) and let
the **Azure SRE agent** detect, diagnose, and recommend a fix.

> See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full architecture.

## Services

| Service | Role | Exposure |
|---|---|---|
| `gateway` | Public BFF, routes to internal services | LoadBalancer |
| `users` | Customer profiles (MySQL) | ClusterIP |
| `products` | Product catalog (MySQL) | ClusterIP |
| `orders` | Places orders; calls users + products; writes to MySQL | ClusterIP |
| `mysql` | Database | StatefulSet + PVC |

## Repo layout

```
services/        # gateway, users, products, orders (+ shared common/)
k8s/             # Kubernetes manifests (incl. MySQL StatefulSet)
scripts/         # load test + alert-rule scripts
.github/workflows/deploy.yml   # CI/CD: build -> ACR -> AKS
docs/ARCHITECTURE.md           # architecture doc
```

---

## Prerequisites

- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli)
- `kubectl` (`az aks install-cli`)
- Docker (for local builds)
- A GitHub repo to host this code

---

## 1. Provision Azure resources

All commands default to region **westus3** and the **ala-shopify** naming prefix.
> **Note:** ACR names must be globally unique and alphanumeric. Change `ACR_NAME`
> if `alashopifyacr` is taken.

```bash
# --- Variables ---
LOCATION=westus3
RESOURCE_GROUP=ala-shopify-rg
ACR_NAME=alashopifyacr
AKS_CLUSTER=ala-shopify-aks
WORKSPACE_NAME=ala-shopify-logs
APPINSIGHTS_NAME=ala-shopify-ai

# --- Resource group ---
az group create --name $RESOURCE_GROUP --location $LOCATION

# --- Log Analytics workspace (for Container Insights + App Insights) ---
az monitor log-analytics workspace create \
  --resource-group $RESOURCE_GROUP \
  --workspace-name $WORKSPACE_NAME \
  --location $LOCATION

WORKSPACE_ID=$(az monitor log-analytics workspace show \
  --resource-group $RESOURCE_GROUP \
  --workspace-name $WORKSPACE_NAME \
  --query id -o tsv)

# --- Application Insights (workspace-based) ---
az extension add --name application-insights --only-show-errors
az monitor app-insights component create \
  --app $APPINSIGHTS_NAME \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --workspace $WORKSPACE_ID

# Grab the connection string for later (used by the app for tracing)
APPINSIGHTS_CONNECTION_STRING=$(az monitor app-insights component show \
  --app $APPINSIGHTS_NAME \
  --resource-group $RESOURCE_GROUP \
  --query connectionString -o tsv)
echo "App Insights connection string: $APPINSIGHTS_CONNECTION_STRING"

# --- Azure Container Registry ---
az acr create \
  --resource-group $RESOURCE_GROUP \
  --name $ACR_NAME \
  --sku Standard

# --- AKS cluster with Container Insights + attached ACR ---
az aks create \
  --resource-group $RESOURCE_GROUP \
  --name $AKS_CLUSTER \
  --location $LOCATION \
  --node-count 2 \
  --node-vm-size Standard_DS2_v2 \
  --enable-addons monitoring \
  --workspace-resource-id $WORKSPACE_ID \
  --attach-acr $ACR_NAME \
  --generate-ssh-keys

# --- Get kubeconfig credentials ---
az aks get-credentials \
  --resource-group $RESOURCE_GROUP \
  --name $AKS_CLUSTER \
  --overwrite-existing
```

---

## 2. Create the deployment service principal (for GitHub Actions)

```bash
SUBSCRIPTION_ID=$(az account show --query id -o tsv)

az ad sp create-for-rbac \
  --name "ala-shopify-gh-deployer" \
  --role contributor \
  --scopes /subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP \
  --sdk-auth
```

Copy the **entire JSON output** — it's the value for the `AZURE_CREDENTIALS`
GitHub secret.

### GitHub secrets to set

In your GitHub repo → **Settings → Secrets and variables → Actions**:

| Secret | Value |
|---|---|
| `AZURE_CREDENTIALS` | The full JSON from `az ad sp create-for-rbac --sdk-auth` |
| `APPINSIGHTS_CONNECTION_STRING` | The App Insights connection string from step 1 (enables tracing) |

> If the names you used differ from the defaults, also update the `env:` block at
> the top of [.github/workflows/deploy.yml](.github/workflows/deploy.yml).

---

## 3. Deploy

### Option A — via GitHub Actions (recommended)

Push to `main`. The workflow builds all 4 images, pushes them to ACR (tagged with
the Git SHA), and deploys to AKS.

```bash
git push origin main
```

### Option B — manually with kubectl

```bash
export ACR_LOGIN_SERVER=$(az acr show --name $ACR_NAME --query loginServer -o tsv)
export IMAGE_TAG=latest

# Build & push images
az acr login --name $ACR_NAME
for svc in gateway users products orders; do
  docker build -f services/$svc/Dockerfile -t $ACR_LOGIN_SERVER/$svc:$IMAGE_TAG .
  docker push $ACR_LOGIN_SERVER/$svc:$IMAGE_TAG
done

# Base resources
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/app-config.yaml
kubectl apply -f k8s/app-secret.yaml
kubectl apply -f k8s/mysql-initdb-configmap.yaml
kubectl apply -f k8s/mysql-statefulset.yaml

# (Optional) inject App Insights connection string for tracing
kubectl -n shopdemo patch secret app-secret --type merge \
  -p "{\"stringData\":{\"APPLICATIONINSIGHTS_CONNECTION_STRING\":\"$APPINSIGHTS_CONNECTION_STRING\"}}"

# Services (envsubst fills in image references)
for svc in products users orders gateway; do
  envsubst < k8s/$svc.yaml | kubectl apply -f -
done
```

---

## 4. Smoke test

```bash
# Wait for the LoadBalancer to get an external IP
kubectl get svc gateway -n shopdemo -w

GATEWAY_IP=$(kubectl get svc gateway -n shopdemo -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

curl http://$GATEWAY_IP/api/products
curl http://$GATEWAY_IP/api/users/1
curl -X POST http://$GATEWAY_IP/api/checkout \
  -H "Content-Type: application/json" \
  -d '{"user_id": 1, "items": [{"product_id": 2, "quantity": 1}]}'
```

---

## 5. Generate load

```bash
# Bash/curl
chmod +x scripts/loadtest.sh
./scripts/loadtest.sh http://$GATEWAY_IP 300 10

# or k6
k6 run -e GATEWAY_URL=http://$GATEWAY_IP scripts/loadtest.js
```

---

## 6. Create alert rules

```bash
chmod +x scripts/create-alerts.sh
RESOURCE_GROUP=$RESOURCE_GROUP \
APPINSIGHTS_NAME=$APPINSIGHTS_NAME \
WORKSPACE_NAME=$WORKSPACE_NAME \
ALERT_EMAIL=you@example.com \
  ./scripts/create-alerts.sh
```

This creates:
- **checkout-5xx-rate** — fires when checkout returns HTTP 5xx
- **checkout-high-latency** — fires when checkout p95 > 800ms
- **shop-pod-restarts** — fires when shopdemo pods restart repeatedly

---

## 7. The SRE agent demo

1. Confirm `main` is deployed and healthy (load test shows fast 200s).
2. Deploy the broken version:
   ```bash
   git checkout release/v2
   git push origin release/v2
   ```
   The workflow rolls out the faulty `orders` service.
3. Keep the load test running. You'll see checkout latency climb and
   intermittent **HTTP 500s**; the alert rules fire.
4. Let the **Azure SRE agent** investigate. Expected findings:
   - Elevated checkout latency + 5xx isolated to the `orders` service.
   - Distributed traces point to a slow database operation on the checkout path.
   - An unhandled edge case causing 500s on certain inputs.
5. Suggested fix: revert/redeploy `main` (or remove the artificial slow query
   and add input handling in `orders`).

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#the-injected-fault-releasev2)
for the exact fault and expected diagnosis.

---

## Cleanup

```bash
az group delete --name $RESOURCE_GROUP --yes --no-wait
```
