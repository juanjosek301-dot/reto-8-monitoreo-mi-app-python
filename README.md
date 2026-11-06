# Café Nube API

Aplicación de pedidos para una cafetería, creada para el reto de monitoreo con Prometheus, Grafana y ELK. Tiene un menú, creación y consulta de pedidos, y un flujo controlado de preparación y entrega. Los pedidos se guardan en memoria para mantener la práctica fácil de levantar; al reiniciar la app, se reinician.

## Requisitos

- Python 3.10 o posterior
- El stack del tutorial, con Logstash escuchando en `localhost:5001`

## Instalar y ejecutar

En PowerShell, desde esta carpeta:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

La aplicación escucha en el puerto 8000. Prometheus puede consultarla desde Docker usando `host.docker.internal:8000`.

## Funcionalidades

- `GET /` y `GET /healthz`: información de la app y estado.
- `GET /api/menu`: lista los productos y precios en COP.
- `GET /api/menu/{product_id}`: consulta un producto.
- `POST /api/pedidos`: crea un pedido, calcula su total y genera un log `INFO`.
- `GET /api/pedidos`: lista pedidos; admite el filtro opcional `?status=PREPARANDO`.
- `GET /api/pedidos/{order_id}`: consulta un pedido.
- `PATCH /api/pedidos/{order_id}/estado`: avanza el pedido según el flujo permitido.
- `GET /api/simulacion/error`: genera un `ERROR` intencional para verificar Kibana.
- `GET /metrics`: métricas Prometheus.
- `GET /docs`: documentación interactiva.

Estados permitidos: `RECIBIDO → PREPARANDO → LISTO → ENTREGADO`. Se puede cancelar un pedido en `RECIBIDO` o `PREPARANDO`. Las transiciones no permitidas responden HTTP 409 y generan un log `ERROR`.

## Probar el flujo

Con la app en ejecución, abre otra ventana de PowerShell y ejecuta:

```powershell
Invoke-RestMethod http://localhost:8000/api/menu
$pedido = Invoke-RestMethod -Method Post -Uri http://localhost:8000/api/pedidos -ContentType 'application/json' -Body '{"product_id":"capuccino","quantity":2,"note":"Leche deslactosada"}'
$pedido
Invoke-RestMethod -Method Patch -Uri "http://localhost:8000/api/pedidos/$($pedido.order_id)/estado" -ContentType 'application/json' -Body '{"status":"PREPARANDO"}'
Invoke-RestMethod http://localhost:8000/api/simulacion/error
```

El último comando devuelve intencionalmente HTTP 500; antes de eso envía el evento `ERROR` a Logstash.

## Métricas y logs

Además de las métricas HTTP `mi_app_http_requests_total` y `mi_app_http_request_duration_seconds`, se publican métricas de pedidos (`mi_app_orders_created_total`, `mi_app_order_value_cop`, `mi_app_orders_current`, `mi_app_order_transitions_total`) y errores controlados (`mi_app_business_errors_total`). Las métricas de negocio alimentan el dashboard `grafana-dashboard.json`.

Cada log se envía como una línea JSON por TCP con `application=mi-app-python`, timestamp UTC, nivel, mensaje y campos de pedido seguros para facilitar búsquedas en Kibana. El destino predeterminado es `localhost:5001`; se puede configurar con las variables de entorno `LOGSTASH_HOST` y `LOGSTASH_PORT`.

## Integrar con Prometheus

Agrega este job a `monitoring/prometheus/prometheus.yml`, conservando el job de Java:

```yaml
- job_name: "mi-app-python"
  metrics_path: "/metrics"
  scrape_interval: 3s
  static_configs:
    - targets: ["host.docker.internal:8000"]
```

Reinicia Prometheus desde la carpeta `monitoring` y confirma que ambos jobs estén `UP` en `http://localhost:9090/targets`. Importa `grafana-dashboard.json` en Grafana y ejecuta varias solicitudes de la sección anterior para poblar los paneles. Luego crea en Kibana un Data View `mi-app-python-*` y otro combinado con el índice de Java.
