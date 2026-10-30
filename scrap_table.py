
import requests
import boto3
import json


def lambda_handler(event, context):

    # URL de la API del IGP
    url = "https://ultimosismo.igp.gob.pe/api/ultimo-sismo/ajaxb/2026"

    # Realizar la solicitud HTTP
    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30
    )

    if response.status_code != 200:
        return {
            'statusCode': response.status_code,
            'body': 'Error al acceder a la API del IGP'
        }

    # Obtener los datos JSON
    rows = response.json()

    # Guardar los datos en DynamoDB
    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table('TablaWebScrappingIGP')

    # Eliminar los elementos anteriores
    scan = table.scan()

    with table.batch_writer() as batch:

        for each in scan['Items']:
            batch.delete_item(
                Key={
                    'id': each['id']
                }
            )

    # Insertar los nuevos datos
    for row in rows:

        row['id'] = str(row['idlistasismos'])

        table.put_item(Item=row)

    # Retornar el resultado como JSON
    return {
        'statusCode': 200,
        'body': json.dumps(rows, ensure_ascii=False)
    }
