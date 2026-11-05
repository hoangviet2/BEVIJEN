#!/usr/bin/env bash
# Resource Engine — provision and deploy everything with the AWS CLI (design §16.3–16.5).
#
#   cd backend && ./infra/deploy.sh <step> [<step> ...]
#   ./infra/deploy.sh all            # every step in order
#
# Steps (each is safe to re-run; "exists" errors are skipped):
#   tf         Terraform for core + iam (infra/terraform); then skip core and iam
#   core       KMS key, S3 bucket (versioning, SSE-KMS, public-access block), DynamoDB tables, SQS + DLQs
#   secrets    re/odrs-api-key (prompted), re/tools-api-key, re/api-key-pepper
#   iam        roles: re-server-role (+ instance profile), re-verifier-agent-role, re-discovery-agent-role,
#              re-gateway-role, re-web-tools-lambda-role, re-scheduler-role
#   cognito    user pool + app client + groups specialist / lead / admin
#   lambda     re-web-tools Lambda (fetch_page, check_site, geocode, places_near, pdf_text)
#   memory     AgentCore Memory (per-organization notes)
#   kb         agent knowledge base: docs/ embedded into S3 Vectors for the search_knowledge tool
#   gateway    AgentCore Gateway (MCP, AWS_IAM, semantic search) + targets + API-key provider + Policy engine
#   identity   Cognito M2M client + JWT inbound auth for the discovery runtime (AgentCore Identity)
#   agents     build/push the agent image (arm64), create/update both AgentCore Runtimes
#   scheduler  EventBridge Scheduler: crawl 15 min, nightly recheck, weekly discovery, daily NDP pull
#   search     OpenSearch Service domain re-search (Priority 5; optional)
#   server     build/push the server image, launch or update the EC2 instance via SSM
#   observability  CloudWatch Transaction Search for AgentCore traces
#   alarms     CloudWatch alarms: DLQ depth, quarantine spikes
#   env        print the env file for the server (write it to /opt/re/.env)
#
# Requires: aws CLI v2 (credentials via SSO/profile), docker (buildx for arm64), jq, zip.
# Nothing here prints secret values.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$(cd "$HERE/.." && pwd)"
export AWS_REGION="${AWS_REGION:-us-west-2}"
export AWS_PAGER=""
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
INDEX_TABLE="${RE_DDB_INDEX_TABLE:-team20-resource-engine-index}"
STATE="$HERE/.state.json"   # ids/arns created here (git-ignored)
[[ -f "$STATE" ]] || echo '{}' > "$STATE"
BUCKET="${RE_S3_BUCKET:-$(jq -r '.bucket // "team20-s3-test-bucket"' "$STATE")}"

log()   { printf '\033[1;34m==> %s\033[0m\n' "$*"; }
get()   { jq -r --arg k "$1" '.[$k] // empty' "$STATE"; }
put()   { local t; t="$(mktemp)"; jq --arg k "$1" --arg v "$2" '.[$k]=$v' "$STATE" > "$t" && mv "$t" "$STATE"; }
quiet() { "$@" >/dev/null 2>&1 || true; }
render() {  # render <file> -> stdout with placeholders filled
  sed -e "s|__ACCOUNT_ID__|$ACCOUNT_ID|g" -e "s|__REGION__|$AWS_REGION|g" -e "s|__BUCKET__|$BUCKET|g" \
      -e "s|__INDEX_TABLE__|$INDEX_TABLE|g" -e "s|__KMS_KEY_ARN__|$(get kms_key_arn)|g" \
      -e "s|__GATEWAY_ARN__|$(get gateway_arn)|g" -e "s|__WEB_TOOLS_LAMBDA_ARN__|$(get web_tools_arn)|g" \
      -e "s|__TOOLS_KEY_PROVIDER_ARN__|$(get tools_key_provider_arn)|g" "$1"
}
tmp() { local f; f="$(mktemp)"; render "$1" > "$f"; echo "$f"; }

# --- tf (alternative to core + iam) -----------------------------------------------------------
step_tf() {
  log "Terraform: KMS, S3, DynamoDB, SQS, IAM (infra/terraform) -> outputs into $STATE"
  terraform -chdir="$HERE/terraform" init -input=false
  terraform -chdir="$HERE/terraform" apply -input=false ${TF_AUTO_APPROVE:+-auto-approve} ${RE_S3_BUCKET:+-var "bucket_name=$RE_S3_BUCKET"}
  local out; out="$(terraform -chdir="$HERE/terraform" output -json)"
  for k in kms_key_arn bucket sqs_verify_url sqs_jobs_url; do put "$k" "$(jq -r --arg k "$k" '.[$k].value' <<<"$out")"; done
  BUCKET="$(get bucket)"
}

# --- core ----------------------------------------------------------------------------------
step_core() {
  log "KMS key"
  if [[ -z "$(get kms_key_arn)" ]]; then
    local arn; arn="$(aws kms create-key --description 'Resource Engine data key' --query KeyMetadata.Arn --output text)"
    quiet aws kms create-alias --alias-name alias/re-data --target-key-id "$arn"
    aws kms enable-key-rotation --key-id "$arn"
    put kms_key_arn "$arn"
  fi

  log "S3 bucket $BUCKET"
  quiet aws s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration "LocationConstraint=$AWS_REGION"
  aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled
  aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
    "{\"Rules\":[{\"ApplyServerSideEncryptionByDefault\":{\"SSEAlgorithm\":\"aws:kms\",\"KMSMasterKeyID\":\"$(get kms_key_arn)\"},\"BucketKeyEnabled\":true}]}"
  aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration '{"Rules":[
    {"ID":"webcache-30d","Status":"Enabled","Filter":{"Prefix":"webcache/"},"Expiration":{"Days":30}},
    {"ID":"old-versions-hsds","Status":"Enabled","Filter":{"Prefix":"hsds/"},"NoncurrentVersionTransitions":[{"NoncurrentDays":90,"StorageClass":"STANDARD_IA"}]}]}'

  log "DynamoDB tables (specs: infra/dynamodb/*.json, generated by python -m app.cli infra-specs)"
  for f in "$HERE"/dynamodb/*.json; do
    local name; name="$(jq -r .TableName "$f")"
    if aws dynamodb describe-table --table-name "$name" >/dev/null 2>&1; then
      echo "  $name exists"
    else
      jq --arg k "$(get kms_key_arn)" '.SSESpecification={"Enabled":true,"SSEType":"KMS","KMSMasterKeyId":$k}' "$f" > "$f.tmp"
      aws dynamodb create-table --cli-input-json "file://$f.tmp" >/dev/null && rm "$f.tmp"
      aws dynamodb wait table-exists --table-name "$name"
      echo "  created $name"
    fi
    aws dynamodb update-continuous-backups --table-name "$name" \
      --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true >/dev/null
  done

  log "SQS"
  for q in re-verify-dlq re-jobs-dlq; do
    aws sqs create-queue --queue-name "$q" --attributes "KmsMasterKeyId=$(get kms_key_arn)" >/dev/null
  done
  put sqs_verify_url "$(aws sqs create-queue --queue-name re-verify-jobs --attributes "file://$(tmp "$HERE/sqs/verify.json")" --query QueueUrl --output text)"
  put sqs_jobs_url "$(aws sqs create-queue --queue-name re-jobs --attributes "file://$(tmp "$HERE/sqs/jobs.json")" --query QueueUrl --output text)"
}

# --- secrets -------------------------------------------------------------------------------
step_secrets() {
  log "Secrets Manager"
  if ! aws secretsmanager describe-secret --secret-id re/odrs-api-key >/dev/null 2>&1; then
    read -r -s -p "ODRS staging API key (input hidden, empty to skip): " ODRS_KEY; echo
    [[ -n "$ODRS_KEY" ]] && aws secretsmanager create-secret --name re/odrs-api-key --kms-key-id "$(get kms_key_arn)" \
      --secret-string "$ODRS_KEY" >/dev/null
  fi
  for s in re/tools-api-key re/api-key-pepper; do
    aws secretsmanager describe-secret --secret-id "$s" >/dev/null 2>&1 || \
      aws secretsmanager create-secret --name "$s" --kms-key-id "$(get kms_key_arn)" \
        --secret-string "$(openssl rand -hex 32)" >/dev/null
  done
}

# --- iam -----------------------------------------------------------------------------------
role() {  # role <name> <trust file> <policy file>
  quiet aws iam create-role --role-name "$1" --assume-role-policy-document "file://$(tmp "$2")"
  aws iam update-assume-role-policy --role-name "$1" --policy-document "file://$(tmp "$2")"
  aws iam put-role-policy --role-name "$1" --policy-name "$1-inline" --policy-document "file://$(tmp "$3")"
}
step_iam() {
  log "IAM roles"
  role re-server-role "$HERE/iam/trust-ec2.json" "$HERE/iam/server-policy.json"
  aws iam attach-role-policy --role-name re-server-role --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
  aws iam attach-role-policy --role-name re-server-role --policy-arn arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy
  quiet aws iam create-instance-profile --instance-profile-name re-server-profile
  quiet aws iam add-role-to-instance-profile --instance-profile-name re-server-profile --role-name re-server-role
  role re-verifier-agent-role "$HERE/iam/trust-agentcore.json" "$HERE/iam/agent-policy.json"
  role re-discovery-agent-role "$HERE/iam/trust-agentcore.json" "$HERE/iam/agent-policy.json"
  role re-gateway-role "$HERE/iam/trust-agentcore.json" "$HERE/iam/gateway-policy.json"
  role re-web-tools-lambda-role "$HERE/iam/trust-lambda.json" "$HERE/iam/lambda-policy.json"
  role re-scheduler-role "$HERE/iam/trust-scheduler.json" "$HERE/iam/scheduler-policy.json"
  sleep 10  # IAM propagation
}

# --- cognito -------------------------------------------------------------------------------
step_cognito() {
  log "Cognito user pool (specialist login)"
  if [[ -z "$(get cognito_pool_id)" ]]; then
    put cognito_pool_id "$(aws cognito-idp create-user-pool --pool-name re-specialists \
      --policies 'PasswordPolicy={MinimumLength=12,RequireUppercase=true,RequireLowercase=true,RequireNumbers=true}' \
      --auto-verified-attributes email --username-attributes email \
      --admin-create-user-config AllowAdminCreateUserOnly=true --query UserPool.Id --output text)"
  fi
  local pool; pool="$(get cognito_pool_id)"
  aws cognito-idp set-user-pool-mfa-config --user-pool-id "$pool" --mfa-configuration OPTIONAL \
    --software-token-mfa-configuration Enabled=true >/dev/null
  for g in specialist lead admin; do quiet aws cognito-idp create-group --user-pool-id "$pool" --group-name "$g"; done
  if [[ -z "$(get cognito_client_id)" ]]; then
    put cognito_client_id "$(aws cognito-idp create-user-pool-client --user-pool-id "$pool" --client-name re-ui \
      --no-generate-secret --explicit-auth-flows ALLOW_USER_SRP_AUTH ALLOW_REFRESH_TOKEN_AUTH \
      --allowed-o-auth-flows code --allowed-o-auth-scopes openid email --allowed-o-auth-flows-user-pool-client \
      --supported-identity-providers COGNITO \
      --callback-urls "${RE_PUBLIC_URL:-https://localhost:8000}/auth/callback" \
      --logout-urls "${RE_PUBLIC_URL:-https://localhost:8000}/ui/" \
      --query UserPoolClient.ClientId --output text)"
  fi
  local domain="re-${ACCOUNT_ID}"
  quiet aws cognito-idp create-user-pool-domain --user-pool-id "$pool" --domain "$domain"
  put cognito_domain "$domain.auth.$AWS_REGION.amazoncognito.com"
  echo "  add a user: aws cognito-idp admin-create-user --user-pool-id $pool --username you@example.org"
  echo "              aws cognito-idp admin-add-user-to-group --user-pool-id $pool --username you@example.org --group-name admin"
}

# --- lambda --------------------------------------------------------------------------------
step_lambda() {
  log "Lambda re-web-tools"
  local dir; dir="$(mktemp -d)"
  cp "$BACKEND/lambdas/web_tools/handler.py" "$BACKEND/agents/tools/web_tools.py" "$dir/"
  (cd "$dir" && zip -q -r web-tools.zip handler.py web_tools.py)
  local role_arn="arn:aws:iam::$ACCOUNT_ID:role/re-web-tools-lambda-role"
  local env; env="$(jq -nc --arg b "$BUCKET" --arg w "${WEBSITE_SOURCE:-sample}" --arg e "${ALERT_EMAIL:-}" \
    --arg t "${ALERT_SMS_TO:-}" --arg o "${SMS_ORIGINATION:-}" \
    '{Variables:{RE_S3_BUCKET:$b,WEBSITE_SOURCE:$w,ALERT_EMAIL:$e,ALERT_SMS_TO:$t,SMS_ORIGINATION:$o}}')"
  if aws lambda get-function --function-name re-web-tools >/dev/null 2>&1; then
    aws lambda update-function-code --function-name re-web-tools --zip-file "fileb://$dir/web-tools.zip" >/dev/null
    aws lambda wait function-updated --function-name re-web-tools
    aws lambda update-function-configuration --function-name re-web-tools --environment "$env" >/dev/null
  else
    aws lambda create-function --function-name re-web-tools --runtime python3.12 --architectures arm64 \
      --handler handler.handler --role "$role_arn" --timeout 150 --memory-size 512 \
      --zip-file "fileb://$dir/web-tools.zip" --environment "$env" --kms-key-arn "$(get kms_key_arn)" >/dev/null
  fi
  put web_tools_arn "$(aws lambda get-function --function-name re-web-tools --query Configuration.FunctionArn --output text)"
}

# --- memory --------------------------------------------------------------------------------
step_memory() {
  log "AgentCore Memory"
  if [[ -z "$(get memory_id)" ]]; then
    put memory_id "$(aws bedrock-agentcore-control create-memory --name re_org_notes \
      --description 'Per-organization notes for Resource Engine agents (namespace org/{id})' \
      --event-expiry-duration 365 --encryption-key-arn "$(get kms_key_arn)" --query memory.id --output text)"
  fi
  echo "  memory id $(get memory_id)"
}

# --- kb ------------------------------------------------------------------------------------
# Agent knowledge (search_knowledge tool): docs/ -> Titan v2 (512) -> S3 Vectors. A managed Bedrock
# Knowledge Base is blocked in the hackathon sandbox (SCP denies CreateKnowledgeBase with any role).
step_kb() {
  log "Agent knowledge base: S3 Vectors re-kb-vectors/re-kb-index"
  quiet aws s3vectors create-vector-bucket --vector-bucket-name re-kb-vectors
  quiet aws s3vectors create-index --vector-bucket-name re-kb-vectors --index-name re-kb-index --data-type float32 \
    --dimension 512 --distance-metric cosine --metadata-configuration 'nonFilterableMetadataKeys=text'
  (cd "$BACKEND" && STORE_BACKEND=memory python -m app.cli kb-load)
}

# --- gateway -------------------------------------------------------------------------------
step_gateway() {
  log "OpenAPI tool spec -> s3://$BUCKET/gateway/openapi-tools.json"
  (cd "$BACKEND" && STORE_BACKEND=memory python -m app.cli gateway-openapi >/dev/null)
  local spec; spec="$(mktemp)"
  sed "s|https://RE_HOST|${RE_PUBLIC_URL:?set RE_PUBLIC_URL to the server HTTPS URL}|" "$BACKEND/gateway/openapi-tools.json" > "$spec"
  aws s3 cp "$spec" "s3://$BUCKET/gateway/openapi-tools.json" --sse aws:kms --sse-kms-key-id "$(get kms_key_arn)" >/dev/null

  log "AgentCore Identity: API-key provider for /tools/*"
  if [[ -z "$(get tools_key_provider_arn)" ]]; then
    local key; key="$(aws secretsmanager get-secret-value --secret-id re/tools-api-key --query SecretString --output text)"
    put tools_key_provider_arn "$(aws bedrock-agentcore-control create-api-key-credential-provider --name re-tools-key \
      --api-key "$key" --query credentialProviderArn --output text)"
    unset key
  fi

  log "Policy engine"
  if [[ -z "$(get policy_engine_arn)" ]]; then
    local pe; pe="$(aws bedrock-agentcore-control create-policy-engine --name re_policy \
      --description 'Resource Engine agent tool permissions' --output json)"
    put policy_engine_id "$(jq -r .policyEngineId <<<"$pe")"
    put policy_engine_arn "$(jq -r .policyEngineArn <<<"$pe")"
  fi

  log "Gateway re-gateway (MCP, AWS_IAM inbound, semantic search — must be set at creation)"
  if [[ -z "$(get gateway_id)" ]]; then
    local gw; gw="$(aws bedrock-agentcore-control create-gateway --name re-gateway \
      --role-arn "arn:aws:iam::$ACCOUNT_ID:role/re-gateway-role" --protocol-type MCP --authorizer-type AWS_IAM \
      --protocol-configuration "file://$BACKEND/gateway/protocol.json" --kms-key-arn "$(get kms_key_arn)" \
      --policy-engine-configuration "arn=$(get policy_engine_arn),mode=${POLICY_MODE:-LOG_ONLY}" --output json)"
    put gateway_id "$(jq -r .gatewayId <<<"$gw")"
    put gateway_arn "$(jq -r .gatewayArn <<<"$gw")"
    put gateway_url "$(jq -r .gatewayUrl <<<"$gw")"
    sleep 20
  fi
  local gid; gid="$(get gateway_id)"
  existing="$(aws bedrock-agentcore-control list-gateway-targets --gateway-identifier "$gid" --query 'items[].name' --output text)"
  if ! grep -qw re-tools <<<"$existing"; then
    aws bedrock-agentcore-control create-gateway-target --gateway-identifier "$gid" --name re-tools \
      --description 'Resource Engine server /tools/* (records, findings, flags, schedules)' \
      --target-configuration "file://$(tmp "$BACKEND/gateway/targets/re-tools.json")" \
      --credential-provider-configurations "file://$(tmp "$BACKEND/gateway/targets/re-tools-credentials.json")" >/dev/null
  fi
  if ! grep -qw web-tools <<<"$existing"; then
    aws bedrock-agentcore-control create-gateway-target --gateway-identifier "$gid" --name web-tools \
      --description 'fetch_page, check_site, geocode, places_near, pdf_text' \
      --target-configuration "file://$(tmp "$BACKEND/gateway/targets/web-tools.json")" \
      --credential-provider-configurations "file://$BACKEND/gateway/targets/lambda-credentials.json" >/dev/null
  fi
  if ! grep -qw assist-tools <<<"$existing"; then
    aws bedrock-agentcore-control create-gateway-target --gateway-identifier "$gid" --name assist-tools \
      --description 'search_knowledge (S3 Vectors), translate_text (Translate), send_message (SES / SMS)' \
      --target-configuration "file://$(tmp "$BACKEND/gateway/targets/assist-tools.json")" \
      --credential-provider-configurations "file://$BACKEND/gateway/targets/lambda-credentials.json" >/dev/null
  fi
  if ! grep -qw web-search <<<"$existing"; then
    aws bedrock-agentcore-control create-gateway-target --gateway-identifier "$gid" --name web-search \
      --description 'AgentCore Web Search connector' \
      --target-configuration "file://$BACKEND/gateway/targets/web-search.json" \
      --credential-provider-configurations "file://$BACKEND/gateway/targets/lambda-credentials.json" >/dev/null
  fi

  log "Cedar policies (gateway/policies/re-agents.cedar) — engine attached in ${POLICY_MODE:-LOG_ONLY} mode"
  sleep 15
  # one policy per statement: split the file on blank-line-separated permit/forbid blocks
  local i=0
  render "$BACKEND/gateway/policies/re-agents.cedar" | python3 -c 'import re,sys; [sys.stdout.write(b+"\0") for b in re.split(r"\n\s*\n", sys.stdin.read()) if re.search(r"\b(permit|forbid)\(", b)]' |
  while IFS= read -r -d '' stmt; do
    i=$((i+1))
    stmt="$(grep -v '^//' <<<"$stmt")"
    [[ -z "${stmt// }" ]] && continue
    local def pid; def="$(jq -n --arg s "$stmt" '{cedar:{statement:$s}}')"
    pid="$(aws bedrock-agentcore-control list-policies --policy-engine-id "$(get policy_engine_id)" \
      --query "policies[?name=='re_agents_$i'].policyId" --output text)"
    if [[ -n "$pid" ]]; then
      aws bedrock-agentcore-control update-policy --policy-engine-id "$(get policy_engine_id)" --policy-id "$pid" \
        --definition "$def" --validation-mode FAIL_ON_ANY_FINDINGS >/dev/null && echo "  policy re_agents_$i updated"
    else
      aws bedrock-agentcore-control create-policy --policy-engine-id "$(get policy_engine_id)" --name "re_agents_$i" \
        --definition "$def" --validation-mode FAIL_ON_ANY_FINDINGS >/dev/null && echo "  policy re_agents_$i created"
    fi
  done
  echo "  after checking the policy decision logs: POLICY_MODE=ENFORCE ./infra/deploy.sh gateway-enforce"
}
step_gateway_enforce() {
  aws bedrock-agentcore-control update-gateway --gateway-identifier "$(get gateway_id)" --name re-gateway \
    --role-arn "arn:aws:iam::$ACCOUNT_ID:role/re-gateway-role" --protocol-type MCP --authorizer-type AWS_IAM \
    --policy-engine-configuration "arn=$(get policy_engine_arn),mode=ENFORCE" >/dev/null
  log "policy engine now ENFORCE"
}

# --- agents --------------------------------------------------------------------------------
ecr_login() { aws ecr get-login-password | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com" >/dev/null; }
step_agents() {
  log "Agent image (linux/arm64, AgentCore Runtime requirement)"
  quiet aws ecr create-repository --repository-name re-agents --image-scanning-configuration scanOnPush=true
  ecr_login
  # Uncommitted changes get a unique tag so the image of the last commit is never overwritten.
  local tag; tag="$(git -C "$BACKEND" rev-parse --short HEAD 2>/dev/null || date +%s)"
  git -C "$BACKEND" diff --quiet HEAD -- "$BACKEND" "$BACKEND/../frontend" 2>/dev/null || tag="$tag-dirty-$(date +%s)"
  local uri="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/re-agents:$tag"
  docker buildx build --platform linux/arm64 -t "$uri" --push "$BACKEND/agents"
  for kind in verifier discovery; do
    local name="re_${kind}_agent" env
    env="$(jq -nc --arg g "$(get gateway_url)" --arg m "${BEDROCK_MODEL_ID:-us.anthropic.claude-sonnet-4-5-20250929-v1:0}" \
      --arg r "$AWS_REGION" --arg b "$BUCKET" --arg a "${kind}_app" --arg w "${WEBSITE_SOURCE:-sample}" \
      '{GATEWAY_URL:$g,BEDROCK_MODEL_ID:$m,AWS_REGION:$r,RE_S3_BUCKET:$b,AGENT_APP:$a,WEBSITE_SOURCE:$w}')"
    # discovery: inbound auth = Cognito JWT via AgentCore Identity (only the server's M2M client), once `identity` ran
    local auth=()
    [[ "$kind" == discovery && -n "$(get m2m_client_id)" ]] && auth=(--authorizer-configuration \
      "{\"customJWTAuthorizer\":{\"discoveryUrl\":\"https://cognito-idp.$AWS_REGION.amazonaws.com/$(get cognito_pool_id)/.well-known/openid-configuration\",\"allowedClients\":[\"$(get m2m_client_id)\"]}}")
    local id out; id="$(get "${kind}_runtime_id")"
    if [[ -z "$id" ]]; then
      out="$(aws bedrock-agentcore-control create-agent-runtime --agent-runtime-name "$name" \
        --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"$uri\"}}" \
        --role-arn "arn:aws:iam::$ACCOUNT_ID:role/re-${kind}-agent-role" ${auth[@]+"${auth[@]}"} \
        --network-configuration networkMode=PUBLIC --environment-variables "$env" --output json)"
      id="$(jq -r .agentRuntimeId <<<"$out")"
      put "${kind}_runtime_id" "$id"
      put "${kind}_agent_arn" "$(jq -r .agentRuntimeArn <<<"$out")"
    else
      out="$(aws bedrock-agentcore-control update-agent-runtime --agent-runtime-id "$id" \
        --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"$uri\"}}" \
        --role-arn "arn:aws:iam::$ACCOUNT_ID:role/re-${kind}-agent-role" ${auth[@]+"${auth[@]}"} \
        --network-configuration networkMode=PUBLIC --environment-variables "$env" --output json)"
    fi
    local ver; ver="$(jq -r .agentRuntimeVersion <<<"$out")"
    local st; until st="$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$id" --query status --output text)"; \
      [[ "$st" == READY || "$st" == *FAILED ]]; do sleep 5; done
    [[ "$st" == READY ]] || { echo "  $name $st: $(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$id" --query failureReason --output text)"; return 1; }
    # named endpoint "live" -> this version (the server calls ?qualifier=live; DEFAULT keeps tracking latest)
    if aws bedrock-agentcore-control get-agent-runtime-endpoint --agent-runtime-id "$id" --endpoint-name live >/dev/null 2>&1; then
      aws bedrock-agentcore-control update-agent-runtime-endpoint --agent-runtime-id "$id" --endpoint-name live \
        --agent-runtime-version "$ver" >/dev/null
    else
      aws bedrock-agentcore-control create-agent-runtime-endpoint --agent-runtime-id "$id" --name live \
        --agent-runtime-version "$ver" --description "Resource Engine production endpoint" >/dev/null
    fi
    echo "  $name v$ver -> $(get "${kind}_agent_arn") (endpoint live)${auth:+, inbound: Cognito JWT}"
  done
}

# --- identity ------------------------------------------------------------------------------
# AgentCore Identity inbound auth for the discovery runtime: Cognito resource server re-agents/invoke
# + an M2M app client (client_credentials) the server uses. Secret: Secrets Manager re/agent-m2m-secret.
step_identity() {
  log "Cognito M2M client for agent runtime JWT auth"
  local pool; pool="$(get cognito_pool_id)"
  quiet aws cognito-idp create-resource-server --user-pool-id "$pool" --identifier re-agents --name "Resource Engine agents" \
    --scopes ScopeName=invoke,ScopeDescription="Invoke agent runtimes"
  if [[ -z "$(get m2m_client_id)" ]]; then
    put m2m_client_id "$(aws cognito-idp create-user-pool-client --user-pool-id "$pool" --client-name re-agent-m2m --generate-secret \
      --allowed-o-auth-flows client_credentials --allowed-o-auth-scopes re-agents/invoke --allowed-o-auth-flows-user-pool-client \
      --access-token-validity 60 --token-validity-units AccessToken=minutes --query UserPoolClient.ClientId --output text)"
  fi
  # copy the client secret into Secrets Manager (jq -j: no trailing newline, which would break Basic auth)
  local f; f="$(mktemp)"
  (umask 077; aws cognito-idp describe-user-pool-client --user-pool-id "$pool" --client-id "$(get m2m_client_id)" \
    --output json | jq -j .UserPoolClient.ClientSecret > "$f")
  aws secretsmanager create-secret --name re/agent-m2m-secret --kms-key-id "$(get kms_key_arn)" \
    --secret-string "file://$f" >/dev/null 2>&1 || aws secretsmanager put-secret-value --secret-id re/agent-m2m-secret \
    --secret-string "file://$f" >/dev/null
  rm -f "$f"
  echo "  m2m client $(get m2m_client_id); now run: ./infra/deploy.sh agents server"
}

# --- scheduler -----------------------------------------------------------------------------
schedule() {  # schedule <name> <expression> <target file>
  local args=(--name "$1" --schedule-expression "$2" --flexible-time-window Mode=OFF --target "file://$(tmp "$3")")
  aws scheduler create-schedule "${args[@]}" >/dev/null 2>&1 || aws scheduler update-schedule "${args[@]}" >/dev/null
  echo "  $1  $2"
}
step_scheduler() {
  log "EventBridge Scheduler -> SQS re-jobs"
  schedule re-crawl "rate(15 minutes)" "$HERE/scheduler/crawl-target.json"
  # Recheck queues every record never checked = one paid agent run each (~$1-2). Opt in: RECHECK=1.
  [[ "${RECHECK:-}" == 1 ]] && schedule re-recheck-nightly "cron(17 9 * * ? *)" "$HERE/scheduler/recheck-target.json"
  schedule re-discovery-weekly "cron(23 10 ? * MON *)" "$HERE/scheduler/discovery-target.json"
  [[ -n "${NDP_MOCK_URL:-}" ]] && schedule re-ndp-daily "cron(7 8 * * ? *)" "$HERE/scheduler/ndp-target.json"
  true
}

# --- search --------------------------------------------------------------------------------
step_search() {
  log "OpenSearch Service domain re-search (takes ~15 min)"
  aws opensearch describe-domain --domain-name re-search >/dev/null 2>&1 || aws opensearch create-domain --domain-name re-search \
    --engine-version OpenSearch_2.19 --cluster-config InstanceType=t3.small.search,InstanceCount=1 \
    --ebs-options EBSEnabled=true,VolumeType=gp3,VolumeSize=10 \
    --encryption-at-rest-options "Enabled=true,KmsKeyId=$(get kms_key_arn)" --node-to-node-encryption-options Enabled=true \
    --domain-endpoint-options EnforceHTTPS=true \
    --access-policies "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Principal\":{\"AWS\":\"arn:aws:iam::$ACCOUNT_ID:role/re-server-role\"},\"Action\":\"es:ESHttp*\",\"Resource\":\"arn:aws:es:$AWS_REGION:$ACCOUNT_ID:domain/re-search/*\"}]}" >/dev/null
  echo "  when active: OPENSEARCH_ENDPOINT=https://\$(aws opensearch describe-domain --domain-name re-search --query DomainStatus.Endpoint --output text)"
  echo "               then: python -m app.cli search-rebuild"
}

# --- server --------------------------------------------------------------------------------
step_server() {
  log "Server image"
  quiet aws ecr create-repository --repository-name re-server --image-scanning-configuration scanOnPush=true
  ecr_login
  # Uncommitted changes get a unique tag so the image of the last commit is never overwritten.
  local tag; tag="$(git -C "$BACKEND" rev-parse --short HEAD 2>/dev/null || date +%s)"
  git -C "$BACKEND" diff --quiet HEAD -- "$BACKEND" "$BACKEND/../frontend" "$BACKEND/../pitch-deck" 2>/dev/null || tag="$tag-dirty-$(date +%s)"
  local uri="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/re-server:$tag"
  log "Specialist UI (frontend/ -> served at /ui)"
  (cd "$BACKEND/../frontend" && npm ci --no-audit --no-fund && npm run build)
  docker buildx build --platform linux/amd64 --build-context ui="$BACKEND/../frontend/dist" \
    --build-context pitch="$BACKEND/../pitch-deck" -t "$uri" --push "$BACKEND"
  step_env > "$HERE/.env.server"
  aws s3 cp "$HERE/.env.server" "s3://$BUCKET/deploy/.env" --sse aws:kms --sse-kms-key-id "$(get kms_key_arn)" >/dev/null
  aws s3 cp "$BACKEND/docker-compose.yml" "s3://$BUCKET/deploy/docker-compose.yml" >/dev/null
  rm -f "$HERE/.env.server"

  if [[ -z "$(get instance_id)" ]]; then
    log "EC2 instance (t3.large, AL2023, IMDSv2, SSM only — no SSH key)"
    : "${SG_ID:?set SG_ID (443 from your IPs / ALB only)}" "${SUBNET_ID:?set SUBNET_ID}"
    put instance_id "$(aws ec2 run-instances \
      --image-id resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
      --instance-type t3.large --iam-instance-profile Name=re-server-profile \
      --security-group-ids "$SG_ID" --subnet-id "$SUBNET_ID" \
      --user-data "file://$(tmp "$HERE/ec2/user-data.sh")" --metadata-options HttpTokens=required,HttpEndpoint=enabled \
      --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=30,VolumeType=gp3,Encrypted=true}' \
      --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=resource-engine}]' \
      --query 'Instances[0].InstanceId' --output text)"
    aws ec2 wait instance-status-ok --instance-ids "$(get instance_id)"
  fi
  [[ -n "${EIP_ALLOC_ID:-}" ]] && aws ec2 associate-address --allocation-id "$EIP_ALLOC_ID" --instance-id "$(get instance_id)" >/dev/null
  log "Deploy $tag via SSM"
  aws ssm send-command --instance-ids "$(get instance_id)" --document-name AWS-RunShellScript \
    --comment "re deploy $tag" --parameters "commands=[
      \"cd /opt/re && aws s3 cp s3://$BUCKET/deploy/.env .env && aws s3 cp s3://$BUCKET/deploy/docker-compose.yml .\",
      \"aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com\",
      \"IMAGE=$uri docker compose pull && IMAGE=$uri docker compose up -d --remove-orphans\"]" \
    --query Command.CommandId --output text
}

# --- observability -------------------------------------------------------------------------
step_observability() {
  log "CloudWatch Transaction Search (AgentCore Observability: agent spans -> aws/spans, GenAI dashboard)"
  aws logs put-resource-policy --policy-name re-transaction-search --policy-document "$(jq -nc --arg a "$ACCOUNT_ID" --arg r "$AWS_REGION" '{Version:"2012-10-17",Statement:[{Sid:"TransactionSearchXRayAccess",Effect:"Allow",Principal:{Service:"xray.amazonaws.com"},Action:"logs:PutLogEvents",Resource:["arn:aws:logs:\($r):\($a):log-group:aws/spans:*","arn:aws:logs:\($r):\($a):log-group:/aws/application-signals/data:*"],Condition:{ArnLike:{"aws:SourceArn":"arn:aws:xray:\($r):\($a):*"},StringEquals:{"aws:SourceAccount":$a}}}]}')" >/dev/null
  [[ "$(aws xray get-trace-segment-destination --query Destination --output text)" == CloudWatchLogs ]] || \
    aws xray update-trace-segment-destination --destination CloudWatchLogs >/dev/null
  aws xray update-indexing-rule --name Default --rule '{"Probabilistic":{"DesiredSamplingPercentage":100}}' >/dev/null
  echo "  traces: CloudWatch > GenAI Observability > Bedrock AgentCore (takes ~10 min to start)"
}

# --- alarms --------------------------------------------------------------------------------
step_alarms() {
  log "CloudWatch alarms (SNS topic re-alarms; subscribe your email)"
  local topic; topic="$(aws sns create-topic --name re-alarms --query TopicArn --output text)"
  for q in re-verify-dlq re-jobs-dlq; do
    aws cloudwatch put-metric-alarm --alarm-name "$q-not-empty" --namespace AWS/SQS --metric-name ApproximateNumberOfMessagesVisible \
      --dimensions "Name=QueueName,Value=$q" --statistic Maximum --period 300 --evaluation-periods 1 --threshold 0 \
      --comparison-operator GreaterThanThreshold --alarm-actions "$topic" --treat-missing-data notBreaching
  done
  aws cloudwatch put-metric-alarm --alarm-name re-quarantine-spike --namespace ResourceEngine --metric-name services_quarantined \
    --dimensions Name=Kind,Value=ingest Name=Source,Value=ndp --statistic Sum --period 3600 --evaluation-periods 1 --threshold 25 \
    --comparison-operator GreaterThanThreshold --alarm-actions "$topic" --treat-missing-data notBreaching
  echo "  aws sns subscribe --topic-arn $topic --protocol email --notification-endpoint you@example.org"
}

# --- env -----------------------------------------------------------------------------------
step_env() {
  cat <<EOF
AWS_REGION=$AWS_REGION
STORE_BACKEND=aws
RE_S3_BUCKET=$BUCKET
RE_KMS_KEY_ID=$(get kms_key_arn)
RE_DDB_INDEX_TABLE=$INDEX_TABLE
RE_SQS_VERIFY_URL=$(get sqs_verify_url)
RE_SQS_JOBS_URL=$(get sqs_jobs_url)
AGENT_MODE=agentcore
VERIFIER_AGENT_ARN=$(get verifier_agent_arn)
DISCOVERY_AGENT_ARN=$(get discovery_agent_arn)
GATEWAY_URL=$(get gateway_url)
AGENTCORE_MEMORY_ID=$(get memory_id)
BEDROCK_MODEL_ID=${BEDROCK_MODEL_ID:-us.anthropic.claude-sonnet-4-5-20250929-v1:0}
AUTH_MODE=cognito
COGNITO_USER_POOL_ID=$(get cognito_pool_id)
COGNITO_CLIENT_ID=$(get cognito_client_id)
COGNITO_DOMAIN=$(get cognito_domain)
WEBSITE_SOURCE=${WEBSITE_SOURCE:-sample}
OPENSEARCH_ENDPOINT=${OPENSEARCH_ENDPOINT:-}
DISCOVERY_AUTH=$([[ -n "$(get m2m_client_id)" ]] && echo jwt || echo sigv4)
DISCOVERY_QUALIFIER=live
AGENT_M2M_CLIENT_ID=$(get m2m_client_id)
COMPOSE_PROFILES=aws
UI_URL=${UI_URL:-/ui/}
CORS_ORIGINS=${CORS_ORIGINS:-}
RE_DOMAIN=$(sed -E 's|^https?://||; s|/.*||' <<<"${RE_PUBLIC_URL:-localhost}")
EOF
}

steps=("$@")
[[ ${#steps[@]} -eq 0 ]] && { sed -n '2,/^# Requires/p' "$0"; exit 1; }
[[ "${steps[0]}" == "all" ]] && steps=(core secrets iam cognito lambda memory kb gateway identity agents scheduler server observability alarms)
for s in "${steps[@]}"; do "step_${s//-/_}"; done
