import random
import uuid

from locust import HttpUser, between, task


class TicketBuyerUser(HttpUser):
    """
    Simulasi user yang membeli tiket secara bersamaan.
    Setiap user menunggu 0.1-0.5 detik antara request.
    """
    wait_time = between(0.1, 0.5)

    @task(weight=10)
    def buy_ticket(self):
        """Task utama: beli tiket — dijalankan 10x lebih sering dari health check."""
        user_id = f"user-{uuid.uuid4().hex[:8]}"

        payload = {
            "user_id": user_id,
            "event_name": "Konser Oasis Sleman",
        }

        with self.client.post(
            "/buy",
            json=payload,
            catch_response=True,
        ) as response:
            # API Gateway + SQS mengembalikan XML, bukan JSON
            # Response 200 dengan SendMessageResponse = sukses
            if response.status_code == 200 and "SendMessageResponse" in response.text:
                response.success()
            else:
                response.failure(f"Unexpected response: {response.status_code} - {response.text[:100]}")

    @task(weight=1)
    def check_queue_status(self):
        """Task sampingan: simulasi user yang refresh halaman status."""
        with self.client.get(
            "/buy",
            catch_response=True,
        ) as response:
            # GET ke /buy akan error tapi kita mark sebagai expected
            response.success()