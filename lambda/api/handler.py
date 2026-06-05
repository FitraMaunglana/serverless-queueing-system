import json
import logging
import os
import uuid

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sqs = boto3.client("sqs")
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["DYNAMODB_TABLE"])
QUEUE_URL = os.environ["SQS_QUEUE_URL"]
MAX_TICKETS_PER_USER = 2

CORS_HEADERS = {
    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Content-Type": "application/json",
}


def response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(body),
    }


def get_user_ticket_count(user_id: str) -> int:
    """Cek total tiket CONFIRMED yang sudah dipesan user ini."""
    result = table.query(
        IndexName="user_id-index",
        KeyConditionExpression=boto3.dynamodb.conditions.Key("user_id").eq(user_id),
        FilterExpression=boto3.dynamodb.conditions.Attr("status").eq("CONFIRMED"),
    )
    return sum(int(item.get("quantity", 1)) for item in result.get("Items", []))


def lambda_handler(event: dict, context) -> dict:
    logger.info(f"Event: {json.dumps(event)}")

    # OPTIONS — preflight CORS
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    if method == "OPTIONS":
        return response(200, {"message": "ok"})

    # Parse body
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return response(400, {"success": False, "message": "Body tidak valid JSON"})

    # Validasi field
    user_id = str(body.get("user_id", "")).strip()
    event_name = str(body.get("event_name", "")).strip()
    quantity = body.get("quantity", 1)

    if not user_id:
        return response(400, {"success": False, "message": "user_id tidak boleh kosong"})
    if not event_name:
        return response(400, {"success": False, "message": "event_name tidak boleh kosong"})

    try:
        quantity = int(quantity)
    except (ValueError, TypeError):
        return response(400, {"success": False, "message": "quantity harus berupa angka"})

    if quantity < 1 or quantity > MAX_TICKETS_PER_USER:
        return response(400, {
            "success": False,
            "message": f"quantity harus antara 1 dan {MAX_TICKETS_PER_USER}",
        })

    # Cek limit per user
    try:
        existing = get_user_ticket_count(user_id)
    except Exception as e:
        logger.error(f"Error cek user count: {str(e)}")
        return response(500, {"success": False, "message": "Gagal cek data user"})

    if existing + quantity > MAX_TICKETS_PER_USER:
        remaining_slot = MAX_TICKETS_PER_USER - existing
        return response(400, {
            "success": False,
            "reason": "LIMIT_EXCEEDED",
            "message": f"Maksimal {MAX_TICKETS_PER_USER} tiket per user. "
                       f"Kamu sudah punya {existing} tiket. "
                       f"Sisa slot: {remaining_slot}.",
            "existing_tickets": existing,
            "remaining_slot": remaining_slot,
        })

    # Cek sisa quota
    try:
        quota_item = table.get_item(Key={"ticket_id": "QUOTA"})
        remaining = int(quota_item.get("Item", {}).get("remaining_tickets", 0))
        if remaining < quantity:
            return response(400, {
                "success": False,
                "reason": "SOLD_OUT",
                "message": f"Tiket tidak cukup. Sisa: {remaining}, kamu minta: {quantity}.",
                "remaining_tickets": remaining,
            })
    except Exception as e:
        logger.error(f"Error cek quota: {str(e)}")
        return response(500, {"success": False, "message": "Gagal cek quota"})

    # Kirim ke SQS
    order_id = str(uuid.uuid4())
    message = {
        "order_id": order_id,
        "user_id": user_id,
        "event_name": event_name,
        "quantity": quantity,
    }

    try:
        sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps(message),
        )
    except ClientError as e:
        logger.error(f"Error kirim SQS: {str(e)}")
        return response(500, {"success": False, "message": "Gagal masukkan ke antrean"})

    logger.info(f"Order masuk antrean | order_id={order_id} user_id={user_id} quantity={quantity}")

    return response(202, {
        "success": True,
        "message": "Pesanan masuk antrean. Sedang diproses...",
        "order_id": order_id,
        "user_id": user_id,
        "event_name": event_name,
        "quantity": quantity,
        "status": "QUEUED",
    })