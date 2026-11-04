locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = "us-west-2"
}

data "aws_caller_identity" "current" {}

# Server role
resource "aws_iam_role" "server" {
  name               = "re-server-role"
  assume_role_policy = file("${path.module}/../iam/trust-ec2.json")
}

resource "aws_iam_role_policy" "server" {
  name = "re-server-role-inline"
  role = aws_iam_role.server.id

  policy = replace(
    replace(
      replace(
        replace(
          replace(
            file("${path.module}/../iam/server-policy.json"),
            "__REGION__", local.region
          ),
          "__ACCOUNT_ID__", local.account_id
        ),
        "__BUCKET__", aws_s3_bucket.resource_engine.bucket
      ),
      "__INDEX_TABLE__", "team20-resource-engine-index"
    ),
    "__KMS_KEY_ARN__", aws_kms_key.re_data.arn
  )
}

resource "aws_iam_role_policy_attachment" "server_ssm" {
  role       = aws_iam_role.server.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "server_cloudwatch" {
  role       = aws_iam_role.server.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

resource "aws_iam_instance_profile" "server" {
  name = "re-server-profile"
  role = aws_iam_role.server.name
}
# Verifier and Discovery AgentCore roles
resource "aws_iam_role" "agent" {
  for_each = toset([
    "re-verifier-agent-role",
    "re-discovery-agent-role"
  ])

  name = each.value

  assume_role_policy = replace(
    replace(
      file("${path.module}/../iam/trust-agentcore.json"),
      "__ACCOUNT_ID__", local.account_id
    ),
    "__REGION__", local.region
  )
}

resource "aws_iam_role_policy" "agent" {
  for_each = aws_iam_role.agent

  name = "${each.key}-inline"
  role = each.value.id

  policy = replace(
    replace(
      replace(
        file("${path.module}/../iam/agent-policy.json"),
        "__REGION__", local.region
      ),
      "__ACCOUNT_ID__", local.account_id
    ),
    "__BUCKET__", aws_s3_bucket.resource_engine.bucket
  )
}
# Gateway role
resource "aws_iam_role" "gateway" {
  name = "re-gateway-role"

  assume_role_policy = replace(
    replace(
      file("${path.module}/../iam/trust-agentcore.json"),
      "__ACCOUNT_ID__", local.account_id
    ),
    "__REGION__", local.region
  )
}

resource "aws_iam_role_policy" "gateway" {
  name = "re-gateway-role-inline"
  role = aws_iam_role.gateway.id

  policy = replace(
    replace(
      replace(
        file("${path.module}/../iam/gateway-policy.json"),
        "__REGION__", local.region
      ),
      "__ACCOUNT_ID__", local.account_id
    ),
    "__BUCKET__", aws_s3_bucket.resource_engine.bucket
  )
}
# Web Tools Lambda role
resource "aws_iam_role" "lambda" {
  name               = "re-web-tools-lambda-role"
  assume_role_policy = file("${path.module}/../iam/trust-lambda.json")
}

resource "aws_iam_role_policy" "lambda" {
  name = "re-web-tools-lambda-role-inline"
  role = aws_iam_role.lambda.id

  policy = replace(
    replace(
      file("${path.module}/../iam/lambda-policy.json"),
      "__REGION__", local.region
    ),
    "__BUCKET__", aws_s3_bucket.resource_engine.bucket
  )
}
# Scheduler role
resource "aws_iam_role" "scheduler" {
  name = "re-scheduler-role"

  assume_role_policy = replace(
    file("${path.module}/../iam/trust-scheduler.json"),
    "__ACCOUNT_ID__", local.account_id
  )
}

resource "aws_iam_role_policy" "scheduler" {
  name = "re-scheduler-role-inline"
  role = aws_iam_role.scheduler.id

  policy = replace(
    replace(
      file("${path.module}/../iam/scheduler-policy.json"),
      "__REGION__", local.region
    ),
    "__ACCOUNT_ID__", local.account_id
  )
}
