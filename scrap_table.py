import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import boto3
import requests

BASE_URL = "https://ultimosismo.igp.gob.pe/api/ultimo-sismo/ajaxb"
REFERER = "https://ultimosismo.igp.gob.pe/productos/reportes-sismicos"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": REFERER,
}
LIMA_TZ = timezone(timedelta(hours=-5))
DEFAULT_LIMIT = 10


def _json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(payload, ensure_ascii=False, default=_json_default),
    }


def _params(event):
    return event.get("queryStringParameters") or event.get("query") or {}


def _parse_int(value, default=None):
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _date_part(value):
    if not value:
        return ""
    return str(value).split("T", 1)[0]


def _time_part(value):
    if not value:
        return ""
    text = str(value)
    if "T" in text:
        text = text.split("T", 1)[1]
    return text.split(".", 1)[0].replace("Z", "")


def _local_datetime(fecha_local, hora_local):
    date = _date_part(fecha_local)
    time = _time_part(hora_local)
    if date and time:
        return f"{date} {time}"
    return date or time


def _decimal(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _semaforo(magnitud):
    if magnitud is None:
        return ""
    if magnitud < Decimal("4.5"):
        return "verde"
    if magnitud < Decimal("6"):
        return "amarillo"
    return "rojo"


def _normalize(item, numero):
    fecha_local = _date_part(item.get("fecha_local"))
    hora_local = _time_part(item.get("hora_local"))
    magnitud = _decimal(item.get("magnitud"))
    codigo = str(item.get("codigo") or "")
    return {
        "id": str(uuid.uuid4()),
        "numero": numero,
        "codigo": codigo,
        "codigo_reporte": f"IGP/CENSIS/RS {codigo}" if codigo else "",
        "fecha_local": fecha_local,
        "hora_local": hora_local,
        "fecha_hora_local": _local_datetime(item.get("fecha_local"), item.get("hora_local")),
        "latitud": _decimal(item.get("latitud")),
        "longitud": _decimal(item.get("longitud")),
        "profundidad": _decimal(item.get("profundidad")),
        "magnitud": magnitud,
        "referencia": str(item.get("referencia") or ""),
        "intensidad": str(item.get("intensidad") or ""),
        "semaforo": _semaforo(magnitud),
        "url_reporte": str(item.get("reporte_acelerometrico_pdf") or ""),
    }


def _fetch_year(year):
    response = requests.get(f"{BASE_URL}/{year}", headers=HEADERS, timeout=20)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict):
        data = data.get("data") or data.get("items") or data.get("results") or []
    if not isinstance(data, list):
        raise ValueError("Respuesta inesperada del API del IGP")
    return data


def _sort_key(item):
    return (
        _date_part(item.get("fecha_local")),
        _time_part(item.get("hora_local")),
        str(item.get("codigo") or ""),
    )


def _replace_table_items(table, rows):
    ids = []
    scan_kwargs = {}
    while True:
        page = table.scan(**scan_kwargs)
        ids.extend(item["id"] for item in page.get("Items", []))
        last_key = page.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    with table.batch_writer() as batch:
        for item_id in ids:
            batch.delete_item(Key={"id": item_id})

    with table.batch_writer() as batch:
        for row in rows:
            batch.put_item(Item=row)


def lambda_handler(event, context):
    params = _params(event)
    now = datetime.now(LIMA_TZ)
    current_year = now.year

    year_param = params.get("year")
    if year_param is None or str(year_param).strip() == "":
        requested_year = None
        years = [current_year, current_year - 1]
    else:
        requested_year = _parse_int(year_param)
        if requested_year is None or requested_year < 1900 or requested_year > current_year + 1:
            return _response(400, {"message": "Parámetro year inválido"})
        years = [requested_year]

    limit_param = params.get("limit")
    if limit_param is None or str(limit_param).strip() == "":
        limit = DEFAULT_LIMIT
    else:
        limit = _parse_int(limit_param)
        if limit is None or limit < 0:
            return _response(400, {"message": "Parámetro limit inválido"})
        if limit == 0:
            limit = None

    reports = []
    for year in years:
        try:
            reports.extend(_fetch_year(year))
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 502
            return _response(status, {"message": "No se pudo obtener los reportes sísmicos"})
        except requests.RequestException:
            return _response(502, {"message": "No se pudo conectar con el servicio del IGP"})
        except ValueError:
            return _response(502, {"message": "Respuesta inválida del servicio del IGP"})
        if limit is None or len(reports) >= limit:
            break

    prepared = [
        item
        for item in reports
        if isinstance(item, dict) and str(item.get("publicado", "1")) == "1"
    ]
    prepared.sort(key=_sort_key, reverse=True)
    if limit is not None:
        prepared = prepared[:limit]

    rows = [_normalize(item, numero) for numero, item in enumerate(prepared, start=1)]

    table_name = os.environ.get("TABLE_NAME", "TablaWebScrappingIGP")
    table = boto3.resource("dynamodb").Table(table_name)
    _replace_table_items(table, rows)

    return _response(
        200,
        {
            "year": requested_year if requested_year is not None else current_year,
            "limit": limit,
            "count": len(rows),
            "items": rows,
        },
    )
