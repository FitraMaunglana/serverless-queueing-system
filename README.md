# Serverless Ticket Queueing System

[![Deploy](https://github.com/FitraMaunglana/serverless-queueing-system/actions/workflows/deploy.yml/badge.svg)](https://github.com/FitraMaunglana/serverless-queueing-system/actions/workflows/deploy.yml)
[![Live Demo](https://img.shields.io/badge/live%20demo-fitramaulana.my.id-brightgreen?style=flat-square)](https://fitramaulana.my.id/#projects)
[![AWS](https://img.shields.io/badge/cloud-AWS-orange?style=flat-square)](https://aws.amazon.com)
[![Terraform](https://img.shields.io/badge/IaC-Terraform-purple?style=flat-square)](https://terraform.io)

A production-grade serverless architecture solving the **ticket war problem** — preventing server crashes and overselling when thousands of users hit "Buy" simultaneously.

🎯 **Live Demo:** [fitramaulana.my.id/#projects](https://fitramaulana.my.id/#projects) → scroll to *Serverless Ticket Queueing System* → click `$ live-demo`

---

## The Problem

When 100,000 users press "Buy Ticket" simultaneously on a traditional architecture:
- Database CPU spikes to 100% → server crashes
- Race conditions cause overselling (selling more tickets than available)
- Revenue loss during peak moments

## The Solution

Decouple request intake from processing using a message queue. The API responds instantly to every user while a Lambda function processes tickets at a controlled rate.
User → API Gateway → SQS Queue → Lambda → DynamoDB
↑ responds in <50ms    ↑ processes async

---

## Architecture
                ┌─────────────────────────────────────┐
                │           AWS Cloud                  │
                │                                      │
User Request ──────►│ API Gateway HTTP API                 │
│      │                               │
│      ▼                               │
│ Lambda (api-handler)                 │
│   • Validate request                 │
│   • Check user limit (max 2)         │
│   • Check quota availability         │
│      │                               │
│      ▼                               │
│ Amazon SQS (Standard Queue)          │
│   • Buffers unlimited requests       │
│   • DLQ after 3 failed attempts      │
│      │                               │
│      ▼ (event trigger)               │
│ Lambda (processor)                   │
│   • Atomic conditional update        │
│   • Anti-overselling guarantee       │
│      │                               │
│      ▼                               │
│ DynamoDB (PAY_PER_REQUEST)           │
│   • Ticket records                   │
│   • Quota counter (QUOTA item)       │
│                                      │
│ Lambda (query)  ── GET /stats        │
│ Lambda (admin)  ── POST /admin/reset │
└─────────────────────────────────────┘

---

## Key Results

| Metric | Result |
|---|---|
| **Throughput** | 290 req/s sustained |
| **Error rate** | 0.00% across 7,251 requests |
| **P99 response time** | 150ms |
| **Anti-overselling** | 200 concurrent requests → exactly 100 confirmed, 0 oversell |
| **API response time** | <50ms (async, non-blocking) |
| **Infrastructure** | 19 AWS resources via Terraform |

---

## Tech Stack

| Layer | Technology |
|---|---|
| API | AWS API Gateway (HTTP API) |
| Queue | Amazon SQS (Standard Queue + DLQ) |
| Compute | AWS Lambda (Python 3.12) × 4 functions |
| Database | Amazon DynamoDB (PAY_PER_REQUEST + GSI) |
| Monitoring | CloudWatch Logs, Metrics, Dashboard, Alarms |
| IaC | Terraform (S3 remote state) |
| CI/CD | GitHub Actions |
| Load Testing | Locust |

---

## API Endpoints

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| `POST` | `/buy` | — | Submit ticket purchase (max 2 per user) |
| `GET` | `/stats` | — | Public system statistics |
| `GET` | `/status/{ticket_id}` | — | Check single ticket status |
| `GET` | `/admin/stats` | `x-admin-key` | Full stats + recent orders |
| `POST` | `/admin/reset` | `x-admin-key` | Reset quota & clear tickets |

### Example: Buy Ticket
```bash
curl -X POST https://<api-endpoint>/production/buy \
  -H "Content-Type: application/json" \
  -d '{"user_id": "john-doe", "event_name": "Menyisir Lirik", "quantity": 2}'
```

Response:
```json
{
  "success": true,
  "message": "Pesanan masuk antrean. Sedang diproses...",
  "order_id": "7f25da69-b89b-4cf8-...",
  "status": "QUEUED"
}
```

### Example: Get Stats
```bash
curl https://<api-endpoint>/production/stats
```

Response:
```json
{
  "success": true,
  "stats": {
    "total_quota": 100,
    "tickets_sold": 47,
    "tickets_remaining": 53,
    "confirmed_orders": 45,
    "sold_out": false,
    "fill_percentage": 47.0
  }
}
```

---

## Repository Structure
ticket-queue-system/
├── terraform/
│   ├── main.tf          # DynamoDB, SQS, Lambda functions
│   ├── api_gateway.tf   # API Gateway routes & integrations
│   ├── iam.tf           # IAM roles & policies
│   ├── monitoring.tf    # CloudWatch dashboard & alarms
│   └── variables.tf     # Configuration variables
├── lambda/
│   ├── api/
│   │   └── handler.py   # Request validation + SQS send
│   ├── processor/
│   │   └── handler.py   # Ticket reservation (atomic)
│   ├── query/
│   │   └── handler.py   # Stats & status endpoints
│   └── admin/
│       └── handler.py   # Admin reset & monitoring
├── load_test/
│   └── locustfile.py    # Load test scenarios
└── .github/
└── workflows/
└── deploy.yml   # CI/CD pipeline

---

## CI/CD Pipeline

Every push to `main` triggers automatic deployment:
push to main
│
├── terraform init
├── terraform validate
├── terraform plan
└── terraform apply  ──► AWS (Lambda + all resources updated)

Terraform state is stored remotely on S3 (`terraform-state-ticket-queue-478111025229`), enabling safe concurrent deployments.

---

## Anti-Overselling Mechanism

The core guarantee is implemented via DynamoDB **conditional atomic update**:

```python
table.update_item(
    Key={"ticket_id": "QUOTA"},
    UpdateExpression="SET remaining_tickets = remaining_tickets - :val",
    ConditionExpression="remaining_tickets >= :val",
    ExpressionAttributeValues={":val": quantity},
)
```

If `remaining_tickets < quantity`, DynamoDB raises `ConditionalCheckFailedException` — the ticket is rejected without any race condition possible.

**Proof:** 200 concurrent requests against 100-ticket quota → exactly 100 confirmed, 0 oversold.

---

## Load Test Results
Tool    : Locust 2.44.1
Users   : 100 concurrent
Duration: 30 seconds
Type     Reqs   Fails   Avg   Min   Max   P99   req/s
POST /buy 6,576  0(0%)  46ms  31ms  239ms 150ms  220
GET  /buy   675  0(0%)  39ms  27ms  212ms 140ms   22
─────────────────────────────────────────────────────
Total    7,251  0(0%)  45ms  27ms  239ms 150ms  243

---

## Local Development

**Prerequisites:** AWS CLI configured, Terraform >= 1.0, Python 3.12

```bash
# Clone repository
git clone https://github.com/FitraMaunglana/serverless-queueing-system.git
cd serverless-queueing-system/ticket-queue-system

# Deploy infrastructure
cd terraform
terraform init
terraform apply

# Run load test
cd ..
pip install locust
locust -f load_test/locustfile.py \
  --host <your-api-endpoint> \
  --headless --users 100 --spawn-rate 10 --run-time 30s
```

---

## Cost

All services operate within **AWS Free Tier** (permanent, not trial):

| Service | Free Tier | Usage |
|---|---|---|
| Lambda | 1M requests/month | ~thousands for demo |
| SQS | 1M requests/month | ~thousands for demo |
| DynamoDB | 25 GB + 25 WCU/RCU | minimal |
| API Gateway | 1M calls/month | ~thousands for demo |

**Estimated monthly cost: $0.00**

---

## Author

**Fitra Maulana** — Cloud & Infrastructure Engineer  
[fitramaulana.my.id](https://fitramaulana.my.id) · [LinkedIn](https://linkedin.com/in/fitra-maulana-32gen8) · [GitHub](https://github.com/FitraMaunglana)