import json
import logging
import os
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

# ─────────────────────────────────────────
# Setup logging
# ─────────────────────────────────────────
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ─────────────────────────────────────────
# AWS clients
# ─────────────────────────────────────────
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["DYNAMODB_TABLE"])

MAX_TICKETS_PER_USER = 2


# ─────────────────────────────────────────
# Helper — Cek total tiket yang sudah dibeli user ini
# ─────────────────────────────────────────
def get_user_ticket_count(user_id: str) -> int:
    """
    Scan tiket CONFIRMED milik user_id ini.
    Kembalikan total quantity yang sudah dipesan.
    """
    response = table.query(
        IndexName="user_id-index",
        KeyConditionExpression=boto3.dynamodb.conditions.Key("user_id").eq(user_id),
        FilterExpression=boto3.dynamodb.conditions.Attr("status").eq("CONFIRMED"),
    )
    total = sum(int(item.get("quantity", 1)) for item in response.get("Items", []))
    return total


# ─────────────────────────────────────────
# Helper — Pesan tiket (atomic, anti-overselling)
# ─────────────────────────────────────────
def reserve_ticket(user_id: str, event_name: str, quantity: int) -> dict:
    """
    Atomic update quota lalu simpan data pemesanan.
    Gagal dengan ConditionalCheckFailedException jika tiket tidak cukup.
    """
    # Cek apakah user sudah pernah beli
    existing_count = get_user_ticket_count(user_id)
    if existing_count + quantity > MAX_TICKETS_PER_USER:
        remaining_allowed = MAX_TICKETS_PER_USER - existing_count
        return {
            "success": False,
            "reason": "LIMIT_EXCEEDED",
            "message": f"Maksimal {MAX_TICKETS_PER_USER} tiket per user. "
                       f"Kamu sudah punya {existing_count} tiket, "
                       f"sisa slot: {remaining_allowed}.",
        }

    ticket_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    # Kurangi quota secara atomic sesuai quantity
    try:
        table.update_item(
            Key={"ticket_id": "QUOTA"},
            UpdateExpression="SET remaining_tickets = remaining_tickets - :val",
            ConditionExpression="remaining_tickets >= :val",
            ExpressionAttributeValues={
                ":val": quantity,
            },
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            # Cek sisa quota untuk pesan error yang informatif
            quota_item = table.get_item(Key={"ticket_id": "QUOTA"})
            remaining = int(quota_item["Item"].get("remaining_tickets", 0))
            return {
                "success": False,
                "reason": "SOLD_OUT",
                "message": f"Tiket tidak cukup. Sisa tiket: {remaining}, "
                           f"kamu minta: {quantity}.",
                "remaining_tickets": remaining,
            }
        raise e

    # Simpan data pemesanan
    table.put_item(
        Item={
            "ticket_id": ticket_id,
            "user_id": user_id,
            "event_name": event_name,
            "quantity": quantity,
            "status": "CONFIRMED",
            "created_at": timestamp,
        }
    )

    logger.info(
        f"Tiket berhasil dipesan | ticket_id={ticket_id} "
        f"user_id={user_id} quantity={quantity}"
    )

    return {
        "success": True,
        "ticket_id": ticket_id,
        "user_id": user_id,
        "event_name": event_name,
        "quantity": quantity,
        "status": "CONFIRMED",
        "created_at": timestamp,
    }


# ─────────────────────────────────────────
# Helper — Validasi body pesan dari SQS
# ─────────────────────────────────────────
def validate_message(body: dict) -> tuple[bool, str]:
    required_fields = ["user_id", "event_name", "quantity"]

    for field in required_fields:
        if field not in body:
            return False, f"Field '{field}' tidak ditemukan"
        if field != "quantity" and not str(body[field]).strip():
            return False, f"Field '{field}' tidak boleh kosong"

    quantity = body["quantity"]
    if not isinstance(quantity, int) or quantity < 1:
        return False, "quantity harus berupa angka positif"
    if quantity > MAX_TICKETS_PER_USER:
        return False, f"quantity maksimal {MAX_TICKETS_PER_USER} tiket"

    return True, ""


# ─────────────────────────────────────────
# Lambda Handler
# ─────────────────────────────────────────
def lambda_handler(event: dict, context) -> dict:
    records = event.get("Records", [])
    logger.info(f"Memproses batch: {len(records)} pesan")

    results = {
        "total": len(records),
        "success": 0,
        "failed": 0,
        "sold_out": 0,
        "limit_exceeded": 0,
    }

    batch_item_failures = []

    for record in records:
        message_id = record["messageId"]

        try:
            body = json.loads(record["body"])
            logger.info(f"Memproses | message_id={message_id} body={body}")

            is_valid, error_msg = validate_message(body)
            if not is_valid:
                logger.warning(f"Pesan tidak valid | {error_msg}")
                results["failed"] += 1
                continue

            result = reserve_ticket(
                user_id=body["user_id"],
                event_name=body["event_name"],
                quantity=int(body["quantity"]),
            )

            if result["success"]:
                results["success"] += 1
                logger.info(f"Sukses | ticket_id={result['ticket_id']}")
            elif result["reason"] == "SOLD_OUT":
                results["sold_out"] += 1
                logger.warning(f"Tiket habis | {result['message']}")
            elif result["reason"] == "LIMIT_EXCEEDED":
                results["limit_exceeded"] += 1
                logger.warning(f"Limit exceeded | {result['message']}")

        except json.JSONDecodeError as e:
            logger.error(f"Gagal parse JSON | message_id={message_id} | {str(e)}")
            results["failed"] += 1
            batch_item_failures.append({"itemIdentifier": message_id})

        except Exception as e:
            logger.error(f"Error | message_id={message_id} | {str(e)}")
            results["failed"] += 1
            batch_item_failures.append({"itemIdentifier": message_id})

    logger.info(f"Hasil batch: {results}")
    return {"batchItemFailures": batch_item_failures}