"""Shared OpenTelemetry / Azure Monitor setup.

Copied into each service image at build time. If the
APPLICATIONINSIGHTS_CONNECTION_STRING env var is not set, telemetry is a
no-op so the services still run locally and in clusters without App Insights.
"""
import logging
import os

logger = logging.getLogger("telemetry")


def setup_telemetry(app, service_name: str) -> None:
    """Wire up Azure Monitor + OpenTelemetry auto-instrumentation.

    Instruments FastAPI (incoming requests), HTTPX (outbound service calls)
    and PyMySQL (database queries) so distributed traces span the whole
    checkout flow.
    """
    conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not conn:
        logger.info("APPLICATIONINSIGHTS_CONNECTION_STRING not set; telemetry disabled")
        return

    os.environ.setdefault("OTEL_SERVICE_NAME", service_name)

    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(connection_string=conn)

        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)

        try:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            HTTPXClientInstrumentor().instrument()
        except Exception:  # pragma: no cover - optional dep
            logger.debug("httpx instrumentation not available")

        try:
            from opentelemetry.instrumentation.pymysql import PyMySQLInstrumentor

            PyMySQLInstrumentor().instrument()
        except Exception:  # pragma: no cover - optional dep
            logger.debug("pymysql instrumentation not available")

        logger.info("Azure Monitor OpenTelemetry configured for %s", service_name)
    except Exception:  # pragma: no cover - never block startup on telemetry
        logger.exception("Failed to configure telemetry; continuing without it")
