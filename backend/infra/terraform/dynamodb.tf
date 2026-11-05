locals {
  dynamodb_tables = {
    for file in fileset("${path.module}/../dynamodb", "*.json") :
    trimsuffix(file, ".json") =>
    jsondecode(file("${path.module}/../dynamodb/${file}"))
  }
}

resource "aws_dynamodb_table" "tables" {
  for_each = local.dynamodb_tables

  name         = each.value.TableName
  billing_mode = each.value.BillingMode

  hash_key = [
    for key in each.value.KeySchema : key.AttributeName
    if key.KeyType == "HASH"
  ][0]

  range_key = try([
    for key in each.value.KeySchema : key.AttributeName
    if key.KeyType == "RANGE"
  ][0], null)

  dynamic "attribute" {
    for_each = each.value.AttributeDefinitions

    content {
      name = attribute.value.AttributeName
      type = attribute.value.AttributeType
    }
  }

  dynamic "global_secondary_index" {
    for_each = try(each.value.GlobalSecondaryIndexes, [])

    content {
      name            = global_secondary_index.value.IndexName
      hash_key        = [for key in global_secondary_index.value.KeySchema : key.AttributeName if key.KeyType == "HASH"][0]
      range_key       = try([for key in global_secondary_index.value.KeySchema : key.AttributeName if key.KeyType == "RANGE"][0], null)
      projection_type = global_secondary_index.value.Projection.ProjectionType
    }
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.re_data.arn
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Project = "Resource Engine"
  }
}
