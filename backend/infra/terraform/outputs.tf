# Read by `infra/deploy.sh tf` into infra/.state.json so the CLI steps reuse these resources.
output "kms_key_arn" { value = aws_kms_key.re_data.arn }
output "bucket" { value = aws_s3_bucket.resource_engine.bucket }
output "sqs_verify_url" { value = aws_sqs_queue.verify.url }
output "sqs_jobs_url" { value = aws_sqs_queue.jobs.url }
