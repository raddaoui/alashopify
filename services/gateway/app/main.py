"""gateway service — public BFF that fans out to the internal services."""
import os

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from common.logging_setup import configure_logging
from common.telemetry import setup_telemetry

logger = configure_logging("gateway")
app = FastAPI(title="gateway")
setup_telemetry(app, "gateway")

USERS_URL = os.getenv("USERS_URL", "http://users")
PRODUCTS_URL = os.getenv("PRODUCTS_URL", "http://products")
ORDERS_URL = os.getenv("ORDERS_URL", "http://orders")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    return {"status": "ready"}


def _proxy_get(url: str):
    resp = httpx.get(url, timeout=HTTP_TIMEOUT)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


@app.get("/api/products")
def products():
    return _proxy_get(f"{PRODUCTS_URL}/products")


@app.get("/api/products/{product_id}")
def product(product_id: int):
    return _proxy_get(f"{PRODUCTS_URL}/products/{product_id}")


@app.get("/api/users/{user_id}")
def user(user_id: int):
    return _proxy_get(f"{USERS_URL}/users/{user_id}")


@app.post("/api/checkout")
async def checkout(request: Request):
    body = await request.json()
    resp = httpx.post(f"{ORDERS_URL}/orders", json=body, timeout=HTTP_TIMEOUT)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


@app.get("/api/orders/{order_id}")
def order(order_id: int):
    return _proxy_get(f"{ORDERS_URL}/orders/{order_id}")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
