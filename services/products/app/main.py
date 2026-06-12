"""products service — product catalog backed by MySQL."""
from fastapi import FastAPI, HTTPException

from common.db import get_connection, ping
from common.logging_setup import configure_logging
from common.telemetry import setup_telemetry

logger = configure_logging("products")
app = FastAPI(title="products")
setup_telemetry(app, "products")


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


@app.get("/products")
def list_products():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, price, stock FROM products ORDER BY id")
            return {"products": cur.fetchall()}
    finally:
        conn.close()


@app.get("/products/{product_id}")
def get_product(product_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, price, stock FROM products WHERE id = %s",
                (product_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="product not found")
    return row
