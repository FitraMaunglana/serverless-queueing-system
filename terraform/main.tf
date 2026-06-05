terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  backend "s3" {
    bucket = "terraform-state-ticket-queue-478111025229"
    key    = "production/terraform.tfstate"
    region = "ap-southeast-1"
  }
}

provider "aws" {
  region = var.aws_region
}

# ─────────────────────────────────────────
# DynamoDB — Penyimpanan data tiket
# ─────────────────────────────────────────
resource "aws_dynamodb_table" "tickets" {
  name         = "${var.project_name}-tickets"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "ticket_id"

  attribute {
    name = "ticket_id"
    type = "S"
  }

  attribute {
    name = "user_id"
    type = "S"
  }

  global_secondary_index {
    name            = "user_id-index"
    hash_key        = "user_id"
    projection_type = "ALL"
  }

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

# Item quota — menyimpan sisa tiket tersedia
resource "aws_dynamodb_table_item" "ticket_quota" {
  table_name = aws_dynamodb_table.tickets.name
  hash_key   = aws_dynamodb_table.tickets.hash_key

  item = jsonencode({
    ticket_id         = { S = "QUOTA" }
    remaining_tickets = { N = tostring(var.ticket_quota) }
  })
}

# ─────────────────────────────────────────
# SQS — Antrean utama + Dead Letter Queue
# ─────────────────────────────────────────
resource "aws_sqs_queue" "ticket_dlq" {
  name                      = "${var.project_name}-dlq"
  message_retention_seconds = 1209600 # 14 hari

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

resource "aws_sqs_queue" "ticket_queue" {
  name                       = "${var.project_name}-queue"
  visibility_timeout_seconds = 30
  message_retention_seconds  = 3600 # 1 jam

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ticket_dlq.arn
    maxReceiveCount     = 3
  })

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

# ─────────────────────────────────────────
# Lambda — Pemroses pesan dari SQS
# ─────────────────────────────────────────
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/../lambda/processor/handler.py"
  output_path = "${path.module}/../lambda/processor/handler.zip"
}

resource "aws_lambda_function" "ticket_processor" {
  function_name    = "${var.project_name}-processor"
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  role             = aws_iam_role.lambda_role.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 30

  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.tickets.name
      ENVIRONMENT    = var.environment
    }
  }

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

# Hubungkan SQS sebagai trigger Lambda
resource "aws_lambda_event_source_mapping" "sqs_trigger" {
  event_source_arn = aws_sqs_queue.ticket_queue.arn
  function_name    = aws_lambda_function.ticket_processor.arn
  batch_size       = var.lambda_batch_size
  enabled          = true
}

# ─────────────────────────────────────────
# Lambda — Query (status & stats publik)
# ─────────────────────────────────────────
data "archive_file" "lambda_query_zip" {
  type        = "zip"
  source_file = "${path.module}/../lambda/query/handler.py"
  output_path = "${path.module}/../lambda/query/handler.zip"
}

resource "aws_lambda_function" "ticket_query" {
  function_name    = "${var.project_name}-query"
  filename         = data.archive_file.lambda_query_zip.output_path
  source_code_hash = data.archive_file.lambda_query_zip.output_base64sha256
  role             = aws_iam_role.lambda_query_role.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 10

  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.tickets.name
      ALLOWED_ORIGIN = "https://fitramaulana.my.id"
      ENVIRONMENT    = var.environment
    }
  }

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

# ─────────────────────────────────────────
# Lambda — Admin (reset quota, stats lengkap)
# ─────────────────────────────────────────
data "archive_file" "lambda_admin_zip" {
  type        = "zip"
  source_file = "${path.module}/../lambda/admin/handler.py"
  output_path = "${path.module}/../lambda/admin/handler.zip"
}

resource "aws_lambda_function" "ticket_admin" {
  function_name    = "${var.project_name}-admin"
  filename         = data.archive_file.lambda_admin_zip.output_path
  source_code_hash = data.archive_file.lambda_admin_zip.output_base64sha256
  role             = aws_iam_role.lambda_admin_role.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 30

  environment {
    variables = {
      DYNAMODB_TABLE   = aws_dynamodb_table.tickets.name
      ALLOWED_ORIGIN   = "https://fitramaulana.my.id"
      ADMIN_SECRET_KEY = var.admin_secret_key
      ENVIRONMENT      = var.environment
    }
  }

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

# ─────────────────────────────────────────
# Lambda — API Handler (validasi + kirim SQS)
# ─────────────────────────────────────────
data "archive_file" "lambda_api_zip" {
  type        = "zip"
  source_file = "${path.module}/../lambda/api/handler.py"
  output_path = "${path.module}/../lambda/api/handler.zip"
}

resource "aws_lambda_function" "ticket_api_handler" {
  function_name    = "${var.project_name}-api-handler"
  filename         = data.archive_file.lambda_api_zip.output_path
  source_code_hash = data.archive_file.lambda_api_zip.output_base64sha256
  role             = aws_iam_role.lambda_api_role.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = 10

  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.tickets.name
      SQS_QUEUE_URL  = aws_sqs_queue.ticket_queue.url
      ALLOWED_ORIGIN = "https://fitramaulana.my.id"
      ENVIRONMENT    = var.environment
    }
  }

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}
