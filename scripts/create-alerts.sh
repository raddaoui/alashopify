#!/usr/bin/env bash
#
# Creates Azure Monitor alert rules for the shop demo so the Azure SRE agent
# (and humans) get notified when a regression degrades checkout.
#
# Prereqs: az CLI logged in, App Insights + Log Analytics already created
# (see README). Set the variables below or export them before running.
#
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-ala-shopify-rg}"
LOCATION="${LOCATION:-westus3}"
APPINSIGHTS_NAME="${APPINSIGHTS_NAME:-ala-shopify-ai}"
WORKSPACE_NAME="${WORKSPACE_NAME:-ala-shopify-logs}"
ALERT_EMAIL="${ALERT_EMAIL:-sre@example.com}"

echo "Resolving resource IDs..."
AI_ID=$(az monitor app-insights component show \
  --app "$APPINSIGHTS_NAME" -g "$RESOURCE_GROUP" --query id -o tsv)
WS_ID=$(az monitor log-analytics workspace show \
  --workspace-name "$WORKSPACE_NAME" -g "$RESOURCE_GROUP" --query id -o tsv)

echo "Creating action group..."
az monitor action-group create \
  --name shop-sre-ag \
  --resource-group "$RESOURCE_GROUP" \
  --short-name shopsre \
  --action email sre "$ALERT_EMAIL"

AG_ID=$(az monitor action-group show \
  --name shop-sre-ag -g "$RESOURCE_GROUP" --query id -o tsv)

echo "Creating high 5xx-rate alert on checkout..."
az monitor scheduled-query create \
  --name "checkout-5xx-rate" \
  --resource-group "$RESOURCE_GROUP" \
  --scopes "$AI_ID" \
  --description "Checkout endpoint is returning HTTP 5xx errors" \
  --condition "count 'Placeholder' > 5" \
  --condition-query Placeholder="requests | where url endswith '/api/checkout' or name contains 'checkout' | where toint(resultCode) >= 500" \
  --evaluation-frequency 5m \
  --window-size 5m \
  --severity 1 \
  --action-groups "$AG_ID"

echo "Creating high latency (p95) alert on checkout..."
az monitor scheduled-query create \
  --name "checkout-high-latency" \
  --resource-group "$RESOURCE_GROUP" \
  --scopes "$AI_ID" \
  --description "Checkout p95 latency above 800ms" \
  --condition "avg 'P95Duration' from 'CheckoutLatency' > 800" \
  --condition-query CheckoutLatency="requests | where url endswith '/api/checkout' or name contains 'checkout' | summarize P95Duration = percentile(duration, 95)" \
  --evaluation-frequency 5m \
  --window-size 5m \
  --severity 2 \
  --action-groups "$AG_ID"

echo "Creating pod-restart alert (Container Insights)..."
az monitor scheduled-query create \
  --name "shop-pod-restarts" \
  --resource-group "$RESOURCE_GROUP" \
  --scopes "$WS_ID" \
  --description "Pods in the shopdemo namespace are restarting" \
  --condition "count 'Restarts' > 3" \
  --condition-query Restarts="KubePodInventory | where Namespace == 'shopdemo' | summarize Restarts = max(PodRestartCount) by Name | where Restarts > 3" \
  --evaluation-frequency 5m \
  --window-size 15m \
  --severity 2 \
  --action-groups "$AG_ID"

echo "Alert rules created."
