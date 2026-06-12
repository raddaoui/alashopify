// k6 load test for the shop demo.
//
// Install k6: https://k6.io/docs/get-started/installation/
//
// Run:
//   k6 run -e GATEWAY_URL=http://<EXTERNAL_IP> scripts/loadtest.js
//
// The checkout path is what degrades on the release/v2 (broken) branch, so the
// p95 latency and http_req_failed thresholds below will start failing once the
// faulty version is deployed — exactly the signal the SRE agent investigates.

import http from 'k6/http';
import { check, sleep } from 'k6';

const GATEWAY_URL = __ENV.GATEWAY_URL || 'http://localhost:8080';

export const options = {
  stages: [
    { duration: '1m', target: 10 },
    { duration: '3m', target: 25 },
    { duration: '1m', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.02'],
    http_req_duration: ['p(95)<800'],
  },
};

export default function () {
  // Browse catalog
  http.get(`${GATEWAY_URL}/api/products`);

  const pid = Math.floor(Math.random() * 10) + 1;
  http.get(`${GATEWAY_URL}/api/products/${pid}`);

  // Checkout
  const uid = Math.floor(Math.random() * 3) + 1;
  const qty = Math.floor(Math.random() * 3) + 1;
  const payload = JSON.stringify({
    user_id: uid,
    items: [{ product_id: pid, quantity: qty }],
  });
  const res = http.post(`${GATEWAY_URL}/api/checkout`, payload, {
    headers: { 'Content-Type': 'application/json' },
  });
  check(res, {
    'checkout status is 200': (r) => r.status === 200,
  });

  sleep(1);
}
