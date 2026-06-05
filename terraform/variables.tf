variable "aws_region" {
  description = "AWS region untuk deploy semua resource"
  type        = string
  default     = "ap-southeast-1"
}

variable "project_name" {
  description = "Nama prefix untuk semua resource"
  type        = string
  default     = "ticket-queue"
}

variable "environment" {
  description = "Environment deployment"
  type        = string
  default     = "production"
}

variable "ticket_quota" {
  description = "Jumlah maksimal tiket yang tersedia"
  type        = number
  default     = 100
}

variable "lambda_batch_size" {
  description = "Jumlah pesan SQS yang diproses Lambda per batch"
  type        = number
  default     = 10
}

variable "admin_secret_key" {
  description = "Secret key untuk endpoint admin reset quota"
  type        = string
  sensitive   = true
}
