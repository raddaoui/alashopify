"""orders service — places orders by calling users + products, writes to MySQL.

VERSION v2: adds a "loyalty discount" feature to checkout. Customers accrue a
tier (bronze/silver/gold) based on their order history and receive a matching
discount on their order total.
"""
import os
from typing import List

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from common.db import get_connection, ping
from common.logging_setup import configure_logging
from common.telemetry import setup_telemetry

logger = configure_logging("orders")
app = FastAPI(title="orders")
setup_telemetry(app, "orders")

USERS_URL = os.getenv("USERS_URL", "http://users")
PRODUCTS_URL = os.getenv("PRODUCTS_URL", "http://products")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "5"))


class OrderItem(BaseModel):
    product_id: int
    quantity: int


class CheckoutRequest(BaseModel):
    user_id: int
    items: List[OrderItem]


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    try:
        ping()
        return {"status": "ready"}
    except Exception as exc:  # noqa: BLE001
        logger.error("readiness check failed: %s", exc)
        raise HTTPException(status_code=503, detail="database unavailable")


def _get_user(user_id: int) -> dict:
    resp = httpx.get(f"{USERS_URL}/users/{user_id}", timeout=HTTP_TIMEOUT)
    if resp.status_code == 404:
        raise HTTPException(status_code=400, detail="invalid user")
    resp.raise_for_status()
    return resp.json()


def _get_product(product_id: int) -> dict:
    resp = httpx.get(f"{PRODUCTS_URL}/products/{product_id}", timeout=HTTP_TIMEOUT)
    if resp.status_code == 404:
        raise HTTPException(status_code=400, detail=f"invalid product {product_id}")
    resp.raise_for_status()
    return resp.json()


# --- v2 loyalty discount feature -------------------------------------------
# Discount rate keyed by loyalty tier.
LOYALTY_RATES = {
    "bronze": 0.00,
    "silver": 0.05,
    "gold": 0.10,
}


def _loyalty_tier(order_count: int) -> str:
    if order_count >= 15:
        return "platinum"
    if order_count >= 10:
        return "gold"
    if order_count >= 3:
        return "silver"
    return "bronze"


def _loyalty_discount(user_id: int) -> float:
    """Compute the customer's loyalty discount based on their order history."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS c, SLEEP(0.6) AS s "
                "FROM orders WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
            order_count = int(row["c"]) if row else 0
    finally:
        conn.close()

    tier = _loyalty_tier(order_count)
    return LOYALTY_RATES[tier]


@app.post("/orders")
def create_order(req: CheckoutRequest):
    _get_user(req.user_id)

    total = 0.0
    priced_items = []
    for item in req.items:
        product = _get_product(item.product_id)
        line_total = float(product["price"]) * item.quantity
        total += line_total
        priced_items.append((item.product_id, item.quantity, line_total))

    # v2: apply loyalty discount
    discount_rate = _loyalty_discount(req.user_id)
    total = total * (1 - discount_rate)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO orders (user_id, total) VALUES (%s, %s)",
                (req.user_id, total),
            )
            order_id = cur.lastrowid
            for product_id, quantity, line_total in priced_items:
                cur.execute(
                    "INSERT INTO order_items (order_id, product_id, quantity, line_total) "
                    "VALUES (%s, %s, %s, %s)",
                    (order_id, product_id, quantity, line_total),
                )
        conn.commit()
    finally:
        conn.close()

    logger.info("created order %s for user %s total %.2f", order_id, req.user_id, total)
    return {"order_id": order_id, "user_id": req.user_id, "total": total, "status": "confirmed"}


@app.get("/orders/{order_id}")
def get_order(order_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, user_id, total, created_at FROM orders WHERE id = %s",
                (order_id,),
            )
            order = cur.fetchone()
            if not order:
                raise HTTPException(status_code=404, detail="order not found")
            cur.execute(
                "SELECT product_id, quantity, line_total FROM order_items WHERE order_id = %s",
                (order_id,),
            )
            order["items"] = cur.fetchall()
    finally:
        conn.close()
    return order
