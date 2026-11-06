"""API de pedidos de una cafetería, instrumentada para Prometheus y ELK."""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from datetime import datetime, timezone
from enum import Enum
from logging import Handler, LogRecord
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)


APPLICATION_NAME = "mi-app-python"
BASE_DIR = Path(__file__).resolve().parent
LOGSTASH_HOST = os.getenv("LOGSTASH_HOST", "localhost")
LOGSTASH_PORT = int(os.getenv("LOGSTASH_PORT", "5001"))

# Métricas HTTP para visualizar latencia y volumen por ruta.
HTTP_REQUESTS = Counter(
    "mi_app_http_requests_total",
    "Solicitudes HTTP procesadas por mi-app-python.",
    ("method", "path", "status"),
)
HTTP_DURATION = Histogram(
    "mi_app_http_request_duration_seconds",
    "Duración de las solicitudes HTTP en segundos.",
    ("method", "path"),
)

# Métricas de negocio con etiquetas de cardinalidad acotada.
ORDERS_CREATED = Counter(
    "mi_app_orders_created_total",
    "Pedidos creados, separados por producto del menú.",
    ("product_id",),
)
ORDER_VALUE = Histogram(
    "mi_app_order_value_cop",
    "Valor de los pedidos en pesos colombianos.",
    buckets=(5000, 10000, 15000, 20000, 30000, 50000, 100000),
)
ORDERS_BY_STATUS = Gauge(
    "mi_app_orders_current",
    "Cantidad actual de pedidos por estado.",
    ("status",),
)
ORDER_TRANSITIONS = Counter(
    "mi_app_order_transitions_total",
    "Transiciones de estado de los pedidos.",
    ("from_status", "to_status"),
)
BUSINESS_ERRORS = Counter(
    "mi_app_business_errors_total",
    "Errores de negocio controlados.",
    ("error_type",),
)


class LogstashJsonHandler(Handler):
    """Envía cada registro como una línea JSON por TCP a Logstash."""

    SAFE_EXTRA_FIELDS = (
        "order_id",
        "product_id",
        "quantity",
        "total_cop",
        "from_status",
        "to_status",
        "error_type",
    )

    def emit(self, record: LogRecord) -> None:
        event: dict[str, Any] = {
            "@timestamp": datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "message": record.getMessage(),
            "application": APPLICATION_NAME,
        }
        for field in self.SAFE_EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                event[field] = value
        if record.exc_info:
            event["exception"] = logging.Formatter().formatException(record.exc_info)

        try:
            with socket.create_connection((LOGSTASH_HOST, LOGSTASH_PORT), timeout=1.0) as connection:
                connection.sendall((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            # La API sigue disponible si Logstash aún no está levantado.
            pass


logger = logging.getLogger(APPLICATION_NAME)
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
    logger.addHandler(LogstashJsonHandler())


class OrderStatus(str, Enum):
    RECIBIDO = "RECIBIDO"
    PREPARANDO = "PREPARANDO"
    LISTO = "LISTO"
    ENTREGADO = "ENTREGADO"
    CANCELADO = "CANCELADO"


class OrderCreate(BaseModel):
    product_id: str = Field(min_length=2, max_length=20, examples=["capuccino"])
    quantity: int = Field(ge=1, le=20, examples=[2])
    note: str | None = Field(default=None, max_length=120, examples=["Leche deslactosada"])


class OrderStatusUpdate(BaseModel):
    status: OrderStatus


MENU: dict[str, dict[str, Any]] = {
    "americano": {"name": "Café americano", "price_cop": 6000, "category": "Café"},
    "capuccino": {"name": "Capuchino", "price_cop": 9000, "category": "Café"},
    "latte": {"name": "Latte", "price_cop": 10000, "category": "Café"},
    "te-chai": {"name": "Té chai", "price_cop": 8500, "category": "Té"},
    "croissant": {"name": "Croissant", "price_cop": 7000, "category": "Panadería"},
}

ORDERS: dict[str, dict[str, Any]] = {}
ORDERS_LOCK = Lock()
ALLOWED_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.RECIBIDO: {OrderStatus.PREPARANDO, OrderStatus.CANCELADO},
    OrderStatus.PREPARANDO: {OrderStatus.LISTO, OrderStatus.CANCELADO},
    OrderStatus.LISTO: {OrderStatus.ENTREGADO},
    OrderStatus.ENTREGADO: set(),
    OrderStatus.CANCELADO: set(),
}
for _status in OrderStatus:
    ORDERS_BY_STATUS.labels(_status.value).set(0)

app = FastAPI(
    title="Café Nube API",
    description=(
        "API de pedidos de una cafetería. Permite consultar el menú, crear pedidos "
        "y seguir su preparación. Instrumentada con Prometheus y ELK para el Reto 8."
    ),
    version="1.0.0",
)


@app.middleware("http")
async def collect_http_metrics(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - started
    route = request.scope.get("route")
    path_template = route.path if route is not None else request.url.path
    HTTP_REQUESTS.labels(request.method, path_template, str(response.status_code)).inc()
    HTTP_DURATION.labels(request.method, path_template).observe(elapsed)
    return response


@app.get("/", tags=["Inicio"])
async def home():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/healthz", tags=["Inicio"])
async def health():
    return {"status": "ok", "application": APPLICATION_NAME}


@app.get("/api/menu", tags=["Menú"])
async def get_menu():
    return [
        {"product_id": product_id, **product}
        for product_id, product in MENU.items()
    ]


@app.get("/api/menu/{product_id}", tags=["Menú"])
async def get_menu_item(product_id: str):
    product = MENU.get(product_id)
    if product is None:
        BUSINESS_ERRORS.labels("producto_no_encontrado").inc()
        logger.error(
            "No se encontró el producto solicitado",
            extra={"product_id": product_id, "error_type": "producto_no_encontrado"},
        )
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return {"product_id": product_id, **product}


@app.post("/api/pedidos", status_code=201, tags=["Pedidos"])
async def create_order(payload: OrderCreate):
    product = MENU.get(payload.product_id)
    if product is None:
        BUSINESS_ERRORS.labels("producto_no_encontrado").inc()
        logger.error(
            "No se creó el pedido porque el producto no existe",
            extra={"product_id": payload.product_id, "error_type": "producto_no_encontrado"},
        )
        raise HTTPException(status_code=404, detail="Producto no encontrado en el menú")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    order_id = str(uuid4())
    total_cop = product["price_cop"] * payload.quantity
    order = {
        "order_id": order_id,
        "product_id": payload.product_id,
        "product_name": product["name"],
        "quantity": payload.quantity,
        "unit_price_cop": product["price_cop"],
        "total_cop": total_cop,
        "note": payload.note,
        "status": OrderStatus.RECIBIDO.value,
        "created_at": now,
        "updated_at": now,
    }

    with ORDERS_LOCK:
        ORDERS[order_id] = order
        ORDERS_BY_STATUS.labels(OrderStatus.RECIBIDO.value).inc()

    ORDERS_CREATED.labels(payload.product_id).inc()
    ORDER_VALUE.observe(total_cop)
    logger.info(
        "Pedido creado",
        extra={
            "order_id": order_id,
            "product_id": payload.product_id,
            "quantity": payload.quantity,
            "total_cop": total_cop,
            "to_status": OrderStatus.RECIBIDO.value,
        },
    )
    return order


@app.get("/api/pedidos", tags=["Pedidos"])
async def list_orders(status: OrderStatus | None = None):
    with ORDERS_LOCK:
        orders = list(ORDERS.values())
    if status is not None:
        orders = [order for order in orders if order["status"] == status.value]
    return sorted(orders, key=lambda order: order["created_at"], reverse=True)


@app.get("/api/pedidos/{order_id}", tags=["Pedidos"])
async def get_order(order_id: str):
    with ORDERS_LOCK:
        order = ORDERS.get(order_id)
        if order is not None:
            return order
    BUSINESS_ERRORS.labels("pedido_no_encontrado").inc()
    logger.error(
        "No se encontró el pedido solicitado",
        extra={"order_id": order_id, "error_type": "pedido_no_encontrado"},
    )
    raise HTTPException(status_code=404, detail="Pedido no encontrado")


@app.patch("/api/pedidos/{order_id}/estado", tags=["Pedidos"])
async def update_order_status(order_id: str, payload: OrderStatusUpdate):
    with ORDERS_LOCK:
        order = ORDERS.get(order_id)
        if order is None:
            current_status = None
        else:
            current_status = OrderStatus(order["status"])
            next_status = payload.status
            if next_status not in ALLOWED_TRANSITIONS[current_status]:
                current_status_value = current_status.value
            else:
                current_status_value = None

            if current_status_value is None:
                old_status = current_status.value
                order["status"] = next_status.value
                order["updated_at"] = (
                    datetime.now(timezone.utc)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z")
                )
                ORDERS_BY_STATUS.labels(old_status).dec()
                ORDERS_BY_STATUS.labels(next_status.value).inc()
                ORDER_TRANSITIONS.labels(old_status, next_status.value).inc()
                updated_order = dict(order)
            else:
                updated_order = None

    if order is None:
        BUSINESS_ERRORS.labels("pedido_no_encontrado").inc()
        logger.error(
            "No se actualizó el estado porque el pedido no existe",
            extra={"order_id": order_id, "error_type": "pedido_no_encontrado"},
        )
        raise HTTPException(status_code=404, detail="Pedido no encontrado")

    if updated_order is None:
        BUSINESS_ERRORS.labels("transicion_invalida").inc()
        logger.error(
            "Transición de estado no permitida",
            extra={
                "order_id": order_id,
                "from_status": current_status.value,
                "to_status": payload.status.value,
                "error_type": "transicion_invalida",
            },
        )
        raise HTTPException(
            status_code=409,
            detail=f"No se puede pasar de {current_status.value} a {payload.status.value}",
        )

    logger.info(
        "Estado del pedido actualizado",
        extra={
            "order_id": order_id,
            "from_status": current_status.value,
            "to_status": payload.status.value,
        },
    )
    return updated_order


@app.get("/api/simulacion/error", tags=["Demostración de logs"])
async def simulate_error():
    """Genera intencionalmente un evento ERROR comprobable en Kibana."""
    BUSINESS_ERRORS.labels("error_demostracion").inc()
    logger.error(
        "Error de demostración solicitado para validar los logs en Kibana",
        extra={"error_type": "error_demostracion"},
    )
    raise HTTPException(status_code=500, detail="Error de demostración para el reto de monitoreo")


@app.get("/metrics", include_in_schema=False)
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
