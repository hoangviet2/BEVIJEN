resource "aws_kms_key" "re_data" {
  description             = "Resource Engine data key"
  enable_key_rotation     = true
  deletion_window_in_days = 7

  tags = {
    Name    = "re-data"
    Project = "Resource Engine"
  }
}

resource "aws_kms_alias" "re_data" {
  name          = "alias/re-data"
  target_key_id = aws_kms_key.re_data.key_id
}

