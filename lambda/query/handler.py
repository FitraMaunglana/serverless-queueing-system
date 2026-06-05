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
# AWS clients
# ─────────────────────────────────────────
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["DYNAMODB_TABLE"])

CORS_HEADERS = {
    "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Content-Type": "application/json",
}


# ─────────────────────────────────────────
# Helper — Convert Decimal ke int/float
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


# ─────────────────────────────────────────
# Handler — GET /status/{ticket_id}
# Cek status satu pesanan berdasarkan ticket_id
# ─────────────────────────────────────────
def get_ticket_status(ticket_id: str) -> dict:
    try:
        result = table.get_item(Key={"ticket_id": ticket_id})
        item = result.get("Item")

        if not item:
            return response(404, {
                "success": False,
                "message": f"Tiket dengan ID '{ticket_id}' tidak ditemukan.",
            })

        return response(200, {
            "success": True,
            "ticket": {
                "ticket_id": item["ticket_id"],
                "user_id": item.get("user_id"),
                "event_name": item.get("event_name"),
                "quantity": item.get("quantity", 1),
                "status": item.get("status"),
                "created_at": item.get("created_at"),
            },
        })

    except ClientError as e:
        logger.error(f"DynamoDB error: {str(e)}")
        return response(500, {"success": False, "message": "Internal server error"})


# ─────────────────────────────────────────
# Handler — GET /stats
# Statistik sistem untuk dashboard publik
# ─────────────────────────────────────────
def get_stats() -> dict:
    try:
        # Ambil sisa quota
        quota_result = table.get_item(Key={"ticket_id": "QUOTA"})
        quota_item = quota_result.get("Item", {})
        remaining = int(quota_item.get("remaining_tickets", 0))

        # Scan semua tiket CONFIRMED
        scan_result = table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr("status").eq("CONFIRMED"),
            ProjectionExpression="quantity",
        )

        confirmed_orders = scan_result.get("Count", 0)
        tickets_sold = sum(
            int(item.get("quantity", 1))
            for item in scan_result.get("Items", [])
        )

        total_quota = remaining + tickets_sold

        return response(200, {
            "success": True,
            "stats": {
                "total_quota": total_quota,
                "tickets_sold": tickets_sold,
                "tickets_remaining": remaining,
                "confirmed_orders": confirmed_orders,
                "sold_out": remaining == 0,
                "fill_percentage": round(
                    (tickets_sold / total_quota * 100) if total_quota > 0 else 0, 1
                ),
            },
        })

    except ClientError as e:
        logger.error(f"DynamoDB error: {str(e)}")
        return response(500, {"success": False, "message": "Internal server error"})


# ─────────────────────────────────────────
# Lambda Handler — Router utama
# ─────────────────────────────────────────
def lambda_handler(event: dict, context) -> dict:
    logger.info(f"Event: {json.dumps(event)}")

    route = event.get("routeKey", "")
    path_params = event.get("pathParameters") or {}

    # OPTIONS — preflight CORS
    if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
        return response(200, {"message": "ok"})

    # GET /stats
    if route == "GET /stats":
        return get_stats()

    # GET /status/{ticket_id}
    if route == "GET /status/{ticket_id}":
        ticket_id = path_params.get("ticket_id", "").strip()
        if not ticket_id:
            return response(400, {
                "success": False,
                "message": "ticket_id tidak boleh kosong",
            })
        return get_ticket_status(ticket_id)

    return response(404, {"success": False, "message": "Route tidak ditemukan"})