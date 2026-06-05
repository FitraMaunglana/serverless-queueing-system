# ─────────────────────────────────────────
# API Gateway — Entry point request user
# ─────────────────────────────────────────
resource "aws_apigatewayv2_api" "ticket_api" {
  name          = "${var.project_name}-api"
  protocol_type = "HTTP"
  description   = "Serverless Ticket Queueing System API"

  cors_configuration {
    allow_origins = [
      "https://fitramaulana.my.id",
      "https://d1c4isbckgsrol.cloudfront.net",
      "http://localhost:3000",
    ]
    allow_methods = ["POST", "GET", "OPTIONS"]
    allow_headers = ["Content-Type", "Authorization", "x-admin-key"]
    max_age       = 300
  }

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

# ─────────────────────────────────────────
# IAM Role — Izin API Gateway kirim ke SQS
# ─────────────────────────────────────────
resource "aws_iam_role" "apigw_sqs_role" {
  name = "${var.project_name}-apigw-sqs-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "apigateway.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "apigw_sqs_policy" {
  name = "${var.project_name}-apigw-sqs-policy"
  role = aws_iam_role.apigw_sqs_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "sqs:SendMessage"
        Resource = aws_sqs_queue.ticket_queue.arn
      }
    ]
  })
}

# ─────────────────────────────────────────
# API Gateway Integration — Langsung ke SQS
# (tanpa Lambda di tengah, lebih efisien)
# ─────────────────────────────────────────
resource "aws_apigatewayv2_integration" "sqs_integration" {
  api_id              = aws_apigatewayv2_api.ticket_api.id
  integration_type    = "AWS_PROXY"
  integration_subtype = "SQS-SendMessage"
  credentials_arn     = aws_iam_role.apigw_sqs_role.arn

  request_parameters = {
    "QueueUrl"    = aws_sqs_queue.ticket_queue.url
    "MessageBody" = "$request.body"
  }

  payload_format_version = "1.0"
}

# ─────────────────────────────────────────
# Routes — Endpoint yang tersedia
# ─────────────────────────────────────────
resource "aws_apigatewayv2_route" "health_check" {
  api_id    = aws_apigatewayv2_api.ticket_api.id
  route_key = "GET /health"
  target    = "integrations/${aws_apigatewayv2_integration.sqs_integration.id}"
}

# ─────────────────────────────────────────
# Stage — Deployment environment
# ─────────────────────────────────────────
resource "aws_apigatewayv2_stage" "production" {
  api_id      = aws_apigatewayv2_api.ticket_api.id
  name        = "production"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.apigw_logs.arn
    format = jsonencode({
      requestId      = "$context.requestId",
      sourceIp       = "$context.identity.sourceIp",
      requestTime    = "$context.requestTime",
      httpMethod     = "$context.httpMethod",
      routeKey       = "$context.routeKey",
      status         = "$context.status",
      responseLength = "$context.responseLength",
      errorMessage   = "$context.error.message",
    })
  }

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

# ─────────────────────────────────────────
# CloudWatch Log Group — Log API Gateway
# ─────────────────────────────────────────
resource "aws_cloudwatch_log_group" "apigw_logs" {
  name              = "/aws/apigateway/${var.project_name}"
  retention_in_days = 7

  tags = {
    Project     = var.project_name
    Environment = var.environment
  }
}

# ─────────────────────────────────────────
# Lambda Permissions — izin API Gateway invoke Lambda
# ─────────────────────────────────────────
resource "aws_lambda_permission" "apigw_query" {
  statement_id  = "AllowAPIGatewayInvokeQuery"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ticket_query.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.ticket_api.execution_arn}/*/*"
}

resource "aws_lambda_permission" "apigw_admin" {
  statement_id  = "AllowAPIGatewayInvokeAdmin"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ticket_admin.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.ticket_api.execution_arn}/*/*"
}

# ─────────────────────────────────────────
# Integrations — Query & Admin Lambda
# ─────────────────────────────────────────
resource "aws_apigatewayv2_integration" "query_integration" {
  api_id                 = aws_apigatewayv2_api.ticket_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ticket_query.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_integration" "admin_integration" {
  api_id                 = aws_apigatewayv2_api.ticket_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ticket_admin.invoke_arn
  payload_format_version = "2.0"
}

# ─────────────────────────────────────────
# Routes — Query (publik)
# ─────────────────────────────────────────
resource "aws_apigatewayv2_route" "get_stats" {
  api_id    = aws_apigatewayv2_api.ticket_api.id
  route_key = "GET /stats"
  target    = "integrations/${aws_apigatewayv2_integration.query_integration.id}"
}

resource "aws_apigatewayv2_route" "get_status" {
  api_id    = aws_apigatewayv2_api.ticket_api.id
  route_key = "GET /status/{ticket_id}"
  target    = "integrations/${aws_apigatewayv2_integration.query_integration.id}"
}

# ─────────────────────────────────────────
# Routes — Admin (protected)
# ─────────────────────────────────────────
resource "aws_apigatewayv2_route" "admin_reset" {
  api_id    = aws_apigatewayv2_api.ticket_api.id
  route_key = "POST /admin/reset"
  target    = "integrations/${aws_apigatewayv2_integration.admin_integration.id}"
}

resource "aws_apigatewayv2_route" "admin_stats" {
  api_id    = aws_apigatewayv2_api.ticket_api.id
  route_key = "GET /admin/stats"
  target    = "integrations/${aws_apigatewayv2_integration.admin_integration.id}"
}

# ─────────────────────────────────────────
# Lambda API Handler — Permission + Integration + Route
# ─────────────────────────────────────────
resource "aws_lambda_permission" "apigw_api_handler" {
  statement_id  = "AllowAPIGatewayInvokeApiHandler"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ticket_api_handler.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.ticket_api.execution_arn}/*/*"
}

resource "aws_apigatewayv2_integration" "api_handler_integration" {
  api_id                 = aws_apigatewayv2_api.ticket_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ticket_api_handler.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "buy_ticket_v2" {
  api_id    = aws_apigatewayv2_api.ticket_api.id
  route_key = "POST /buy"
  target    = "integrations/${aws_apigatewayv2_integration.api_handler_integration.id}"
}

# ─────────────────────────────────────────
# Output — URL API yang bisa dipakai
# ─────────────────────────────────────────
output "api_endpoint" {
  description = "URL endpoint API Gateway"
  value       = aws_apigatewayv2_stage.production.invoke_url
}

output "sqs_queue_url" {
  description = "URL SQS Queue"
  value       = aws_sqs_queue.ticket_queue.url
}

output "dynamodb_table_name" {
  description = "Nama DynamoDB table"
  value       = aws_dynamodb_table.tickets.name
}
