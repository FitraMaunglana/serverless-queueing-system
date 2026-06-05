import json
import logging
import os
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

# ─────────────────────────────────────────
# Setup logging
# ─────────────────────────────────────────
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ─────────────────────────────────────────
# AWS clients & config
# ─────────────────────────────────────────
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["DYNAMODB_TABLE"])
ADMIN_SECRET_KEY = os.environ.get("ADMIN_SECRET_KEY", "")

CORS_HEADERS = {
    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type, x-admin-key",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Content-Type": "application/json",
}


# ─────────────────────────────────────────
# Helper
# ─────────────────────────────────────────
def decimal_to_number(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(body, default=decimal_to_number),
    }


def is_authorized(event: dict) -> bool:
    """Cek x-admin-key dari header request."""
    headers = event.get("headers") or {}
    provided_key = headers.get("x-admin-key", "").strip()

    if not ADMIN_SECRET_KEY:
        logger.error("ADMIN_SECRET_KEY tidak di-set di environment variables!")
        return False

    return provided_key == ADMIN_SECRET_KEY


# ─────────────────────────────────────────
# Handler — POST /admin/reset
# Reset quota tiket ke nilai baru
# ─────────────────────────────────────────
def reset_quota(new_quota: int) -> dict:
    if new_quota < 1 or new_quota > 10000:
        return response(400, {
            "success": False,
            "message": "Quota harus antara 1 dan 10000",
        })

    try:
        # Hapus semua tiket CONFIRMED terlebih dahulu
        scan_result = table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr("status").eq("CONFIRMED"),
            ProjectionExpression="ticket_id",
        )

        deleted_count = 0
        with table.batch_writer() as batch:
            for item in scan_result.get("Items", []):
                batch.delete_item(Key={"ticket_id": item["ticket_id"]})
                deleted_count += 1

        # Reset quota ke nilai baru
        table.update_item(
            Key={"ticket_id": "QUOTA"},
            UpdateExpression="SET remaining_tickets = :val",
            ExpressionAttributeValues={":val": new_quota},
        )

        logger.info(
            f"Quota direset | new_quota={new_quota} "
            f"deleted_tickets={deleted_count}"
        )

        return response(200, {
            "success": True,
            "message": f"Quota berhasil direset ke {new_quota} tiket.",
            "new_quota": new_quota,
            "deleted_tickets": deleted_count,
        })

    except ClientError as e:
        logger.error(f"DynamoDB error saat reset: {str(e)}")
        return response(500, {"success": False, "message": "Internal server error"})


# ─────────────────────────────────────────
# Handler — GET /admin/stats
# Statistik lengkap untuk operator
# ─────────────────────────────────────────
def get_admin_stats() -> dict:
    try:
        # Ambil quota
        quota_result = table.get_item(Key={"ticket_id": "QUOTA"})
        quota_item = quota_result.get("Item", {})
        remaining = int(quota_item.get("remaining_tickets", 0))

        # Scan semua tiket
        scan_result = table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr("status").eq("CONFIRMED"),
        )

        items = scan_result.get("Items", [])
        tickets_sold = sum(int(item.get("quantity", 1)) for item in items)
        total_quota = remaining + tickets_sold

        # Susun list pemesanan terbaru (10 terakhir)
        sorted_items = sorted(
            items,
            key=lambda x: x.get("created_at", ""),
            reverse=True,
        )[:10]

        recent_orders = [
            {
                "ticket_id": item["ticket_id"],
                "user_id": item.get("user_id"),
                "event_name": item.get("event_name"),
                "quantity": item.get("quantity", 1),
                "created_at": item.get("created_at"),
            }
            for item in sorted_items
        ]

        return response(200, {
            "success": True,
            "stats": {
                "total_quota": total_quota,
                "tickets_sold": tickets_sold,
                "tickets_remaining": remaining,
                "confirmed_orders": len(items),
                "sold_out": remaining == 0,
                "fill_percentage": round(
                    (tickets_sold / total_quota * 100) if total_quota > 0 else 0, 1
                ),
            },
            "recent_orders": recent_orders,
        })

    except ClientError as e:
        logger.error(f"DynamoDB error: {str(e)}")
        return response(500, {"success": False, "message": "Internal server error"})


# ─────────────────────────────────────────
# Lambda Handler — Router utama
# ─────────────────────────────────────────
def lambda_handler(event: dict, context) -> dict:
    logger.info(f"Route: {event.get('routeKey')} | IP: {event.get('requestContext', {}).get('http', {}).get('sourceIp')}")

    # OPTIONS — preflight CORS
    if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
        return response(200, {"message": "ok"})

    # Semua route admin butuh autentikasi
    if not is_authorized(event):
        logger.warning("Unauthorized access attempt")
        return response(401, {
            "success": False,
            "message": "Unauthorized. x-admin-key tidak valid.",
        })

    route = event.get("routeKey", "")

    # POST /admin/reset
    if route == "POST /admin/reset":
        try:
            body = json.loads(event.get("body") or "{}")
            new_quota = int(body.get("quota", 100))
        except (json.JSONDecodeError, ValueError):
            return response(400, {
                "success": False,
                "message": "Body tidak valid. Contoh: {\"quota\": 100}",
            })
        return reset_quota(new_quota)

    # GET /admin/stats
    if route == "GET /admin/stats":
        return get_admin_stats()

    return response(404, {"success": False, "message": "Route tidak ditemukan"})