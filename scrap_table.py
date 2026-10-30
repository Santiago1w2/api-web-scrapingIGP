
import requests
from bs4 import BeautifulSoup
import boto3
import uuid
import json


def lambda_handler(event, context):

    url = "https://ultimosismo.igp.gob.pe/productos/reportes-sismicos"

    # Solicitar la pagina
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30
    )

    if response.status_code != 200:
        return {
            "statusCode": response.status_code,
            "body": "Error al acceder a la pagina del IGP"
        }

    # Procesar el HTML
    soup = BeautifulSoup(response.text, "html.parser")

    # Buscar la tabla de reportes
    tabla_html = soup.find("table")

    if tabla_html is None:
        return {
            "statusCode": 404,
            "body": "No se encontro la tabla en el HTML. Puede cargarse con JavaScript."
        }

    # Extraer los encabezados
    headers = [
        th.get_text(" ", strip=True)
        for th in tabla_html.find_all("th")
    ]

    # Extraer las filas
    rows = []

    for tr in tabla_html.find_all("tr")[1:]:

        cells = tr.find_all("td")

        if not cells:
            continue

        row = {}

        for i, cell in enumerate(cells):

            if i < len(headers):
                row[headers[i]] = cell.get_text(" ", strip=True)

            # Guardar el enlace del reporte, si existe
            enlace = cell.find("a", href=True)

            if enlace:
                row["Enlace"] = enlace["href"]

        row["id"] = str(uuid.uuid4())
        rows.append(row)

    # Guardar en DynamoDB
    dynamodb = boto3.resource("dynamodb")
    tabla = dynamodb.Table("TablaWebScrappingIGP")

    with tabla.batch_writer() as batch:

        for row in rows:
            batch.put_item(Item=row)

    # Respuesta
    return {
        "statusCode": 200,
        "body": json.dumps(rows, ensure_ascii=False)
    }
