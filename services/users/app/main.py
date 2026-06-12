"""users service — customer profiles backed by MySQL."""
from fastapi import FastAPI, HTTPException

from common.db import get_connection, ping
from common.logging_setup import configure_logging
from common.telemetry import setup_telemetry

logger = configure_logging("users")
app = FastAPI(title="users")
setup_telemetry(app, "users")


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


@app.get("/users/{user_id}")
def get_user(user_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, email FROM users WHERE id = %s",
                (user_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="user not found")
    return row
