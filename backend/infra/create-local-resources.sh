#!/usr/bin/env bash
# Create the bucket, tables and queues in LocalStack (or a sandbox account).
#   export RE_AWS_ENDPOINT_URL=http://localhost:4566   # LocalStack
#   ./infra/create-local-resources.sh
# Then run the server with STORE_BACKEND=aws and the printed queue URLs.
set -euo pipefail
REGION="${AWS_REGION:-us-west-2}"
BUCKET="${RE_S3_BUCKET:-team20-s3-test-bucket}"
EP=(); [[ -n "${RE_AWS_ENDPOINT_URL:-}" ]] && EP=(--endpoint-url "$RE_AWS_ENDPOINT_URL")
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
aws "${EP[@]}" s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
  --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null 2>&1 || true
for f in "$HERE"/dynamodb/*.json; do
  aws "${EP[@]}" dynamodb create-table --region "$REGION" --cli-input-json "file://$f" >/dev/null 2>&1 \
    && echo "table $(jq -r .TableName "$f")" || echo "table $(jq -r .TableName "$f") exists"
done
for q in re-verify-jobs re-jobs; do
  echo "RE_SQS_$( [[ $q == re-jobs ]] && echo JOBS || echo VERIFY )_URL=$(aws "${EP[@]}" sqs create-queue --region "$REGION" --queue-name "$q" --query QueueUrl --output text)"
done
