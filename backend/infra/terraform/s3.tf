variable "bucket_name" {
  description = "S3 bucket name (globally unique)"
  type        = string
  default     = "team20-resource-engine-2026"
}

resource "aws_s3_bucket" "resource_engine" {
  bucket = var.bucket_name

  tags = {
    Name    = "Resource Engine"
    Project = "Resource Engine"
  }
}

resource "aws_s3_bucket_versioning" "resource_engine" {
  bucket = aws_s3_bucket.resource_engine.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "resource_engine" {
  bucket = aws_s3_bucket.resource_engine.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.re_data.arn
    }

    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "resource_engine" {
  bucket = aws_s3_bucket.resource_engine.id

  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "resource_engine" {
  bucket = aws_s3_bucket.resource_engine.id

  rule {
    id     = "webcache-30d"
    status = "Enabled"

    filter {
      prefix = "webcache/"
    }

    expiration {
      days = 30
    }
  }

  rule {
    id     = "old-versions-hsds"
    status = "Enabled"

    filter {
      prefix = "hsds/"
    }

    noncurrent_version_transition {
      noncurrent_days = 90
      storage_class   = "STANDARD_IA"
    }
  }

  depends_on = [aws_s3_bucket_versioning.resource_engine]
}
