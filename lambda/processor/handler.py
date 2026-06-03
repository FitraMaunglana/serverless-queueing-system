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


# ─────────────────────────────────────────
# Helper — Kurangi quota tiket (atomic)
# ─────────────────────────────────────────
def reserve_ticket(user_id: str, event_name: str) -> dict:
    """
    Menggunakan conditional update agar tidak terjadi overselling.
    Jika remaining_tickets sudah 0, operasi gagal dengan ConditionalCheckFailedException.
    """
    ticket_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    # Langkah 1: Kurangi quota secara atomic
    try:
        table.update_item(
            Key={"ticket_id": "QUOTA"},
            UpdateExpression="SET remaining_tickets = remaining_tickets - :val",
            ConditionExpression="remaining_tickets > :zero",
            ExpressionAttributeValues={
                ":val": 1,
                ":zero": 0,
            },
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return {
                "success": False,
                "reason": "SOLD_OUT",
                "message": "Tiket sudah habis terjual",
            }
        raise e

    # Langkah 2: Simpan data pemesanan
    table.put_item(
        Item={
            "ticket_id": ticket_id,
            "user_id": user_id,
            "event_name": event_name,
            "status": "CONFIRMED",
            "created_at": timestamp,
        }
    )

    logger.info(f"Tiket berhasil dipesan | ticket_id={ticket_id} user_id={user_id}")

    return {
        "success": True,
        "ticket_id": ticket_id,
        "user_id": user_id,
        "event_name": event_name,
        "status": "CONFIRMED",
        "created_at": timestamp,
    }


# ─────────────────────────────────────────
# Helper — Validasi isi pesan dari SQS
# ─────────────────────────────────────────
def validate_message(body: dict) -> tuple[bool, str]:
    required_fields = ["user_id", "event_name"]

    for field in required_fields:
        if field not in body:
            return False, f"Field '{field}' tidak ditemukan dalam request"
        if not isinstance(body[field], str) or not body[field].strip():
            return False, f"Field '{field}' tidak boleh kosong"

    return True, ""


# ─────────────────────────────────────────
# Lambda Handler — Entry point utama
# ─────────────────────────────────────────
def lambda_handler(event: dict, context) -> dict:
    """
    Dipanggil otomatis oleh AWS setiap kali ada pesan baru di SQS.
    Satu invokasi bisa memproses beberapa pesan sekaligus (batch).
    """
    records = event.get("Records", [])
    logger.info(f"Memproses batch: {len(records)} pesan")

    results = {
        "total": len(records),
        "success": 0,
        "failed": 0,
        "sold_out": 0,
    }

    batch_item_failures = []

    for record in records:
        message_id = record["messageId"]

        try:
            # Parse body pesan dari SQS
            body = json.loads(record["body"])
            logger.info(f"Memproses pesan | message_id={message_id} body={body}")

            # Validasi field
            is_valid, error_msg = validate_message(body)
            if not is_valid:
                logger.warning(f"Pesan tidak valid | message_id={message_id} error={error_msg}")
                results["failed"] += 1
                continue

            # Proses pemesanan tiket
            result = reserve_ticket(
                user_id=body["user_id"],
                event_name=body["event_name"],
            )

            if result["success"]:
                results["success"] += 1
                logger.info(f"Sukses | ticket_id={result['ticket_id']}")
            else:
                results["sold_out"] += 1
                logger.warning(f"Tiket habis | user_id={body['user_id']}")

        except json.JSONDecodeError as e:
            logger.error(f"Gagal parse JSON | message_id={message_id} error={str(e)}")
            results["failed"] += 1
            # Masukkan ke batch failures agar SQS kirim ke DLQ
            batch_item_failures.append({"itemIdentifier": message_id})

        except Exception as e:
            logger.error(f"Error tidak terduga | message_id={message_id} error={str(e)}")
            results["failed"] += 1
            batch_item_failures.append({"itemIdentifier": message_id})

    logger.info(f"Hasil batch: {results}")

    # Kembalikan pesan gagal ke SQS untuk dicoba ulang (max 3x lalu ke DLQ)
    return {"batchItemFailures": batch_item_failures}