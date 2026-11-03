#!/usr/bin/env bash
# Create the S3 bucket and DynamoDB table locally (LocalStack) or in AWS.
# Requires AWS CLI. Set RE_AWS_ENDPOINT_URL for LocalStack, e.g.
#   export RE_AWS_ENDPOINT_URL=http://localhost:4566
set -euo pipefail

REGION="${AWS_REGION:-us-west-2}"
BUCKET="${RE_S3_BUCKET:-resource-engine-hsds}"
TABLE="${RE_DDB_TABLE:-resource-engine-index}"
ENDPOINT_ARG=""
if [[ -n "${RE_AWS_ENDPOINT_URL:-}" ]]; then
  ENDPOINT_ARG="--endpoint-url ${RE_AWS_ENDPOINT_URL}"
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Creating S3 bucket: ${BUCKET}"
aws ${ENDPOINT_ARG} s3api create-bucket \
  --bucket "${BUCKET}" \
  --region "${REGION}" \
  --create-bucket-configuration "LocationConstraint=${REGION}" 2>/dev/null \
  || aws ${ENDPOINT_ARG} s3api create-bucket --bucket "${BUCKET}" --region "${REGION}"

echo "Creating DynamoDB table: ${TABLE}"
aws ${ENDPOINT_ARG} dynamodb create-table \
  --cli-input-json "file://${HERE}/dynamodb-table.json" \
  --table-name "${TABLE}" \
  --region "${REGION}"

echo "Done."
