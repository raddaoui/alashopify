#!/usr/bin/env bash
#
# Simple load generator for the shop demo. Drives traffic through the gateway
# so the Azure SRE agent has live signals (latency + 5xx) to analyze.
#
# Usage:
#   ./loadtest.sh <GATEWAY_URL> [DURATION_SECONDS] [CONCURRENCY]
#
# Example:
#   ./loadtest.sh http://20.10.20.30 300 10
#
set -euo pipefail

GATEWAY_URL="${1:-http://localhost:8080}"
DURATION="${2:-180}"
CONCURRENCY="${3:-10}"

echo "Load testing $GATEWAY_URL for ${DURATION}s with ${CONCURRENCY} workers"
echo "Press Ctrl+C to stop early."

end=$(( $(date +%s) + DURATION ))

worker() {
  while [ "$(date +%s)" -lt "$end" ]; do
    # Browse catalog
    curl -s -o /dev/null -w "GET /api/products -> %{http_code} (%{time_total}s)\n" \
      "$GATEWAY_URL/api/products" || true

    # Look at a random product
    pid=$(( (RANDOM % 10) + 1 ))
    curl -s -o /dev/null -w "GET /api/products/$pid -> %{http_code} (%{time_total}s)\n" \
      "$GATEWAY_URL/api/products/$pid" || true

    # Checkout (this is the path that degrades on release/v2)
    uid=$(( (RANDOM % 3) + 1 ))
    qty=$(( (RANDOM % 3) + 1 ))
    curl -s -o /dev/null -w "POST /api/checkout -> %{http_code} (%{time_total}s)\n" \
      -H "Content-Type: application/json" \
      -d "{\"user_id\": $uid, \"items\": [{\"product_id\": $pid, \"quantity\": $qty}]}" \
      "$GATEWAY_URL/api/checkout" || true
  done
}

for _ in $(seq 1 "$CONCURRENCY"); do
  worker &
done

wait
echo "Load test complete."
