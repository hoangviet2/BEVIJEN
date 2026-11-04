resource "aws_sqs_queue" "jobs_dlq" {
  name              = "re-jobs-dlq"
  kms_master_key_id = aws_kms_key.re_data.arn
}

resource "aws_sqs_queue" "verify_dlq" {
  name              = "re-verify-dlq"
  kms_master_key_id = aws_kms_key.re_data.arn
}

resource "aws_sqs_queue" "jobs" {
  name                       = "re-jobs"
  visibility_timeout_seconds = 1800
  receive_wait_time_seconds  = 20
  kms_master_key_id          = aws_kms_key.re_data.arn

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.jobs_dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue" "verify" {
  name                       = "re-verify-jobs"
  visibility_timeout_seconds = 900
  message_retention_seconds  = 345600
  receive_wait_time_seconds  = 20
  kms_master_key_id          = aws_kms_key.re_data.arn

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.verify_dlq.arn
    maxReceiveCount     = 3
  })
}
