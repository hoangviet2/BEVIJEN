"""Environment-driven configuration (RESOURCE-ENGINE-DESIGN.md §15).

Every value can be overridden with an environment variable of the same name.
`STORE_BACKEND=memory` runs the whole server with in-process fakes for S3,
DynamoDB and SQS (local dev and tests); `aws` (default) uses real AWS.
"""

from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field
from functools import lru_cache


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # --- core -------------------------------------------------------------
    hsds_version: str = field(default_factory=lambda: _env("RE_HSDS_VERSION", "3.2.3"))
    aws_region: str = field(default_factory=lambda: _env("AWS_REGION", _env("AWS_DEFAULT_REGION", "us-west-2")))
    aws_endpoint_url: str = field(default_factory=lambda: _env("RE_AWS_ENDPOINT_URL"))  # LocalStack
    store_backend: str = field(default_factory=lambda: _env("STORE_BACKEND", "aws"))  # aws | memory
    # Never change: every derived id depends on it (R1.6).
    id_namespace: str = field(default_factory=lambda: _env("RE_ID_NAMESPACE", "6ba7b810-9dad-11d1-80b4-00c04fd430c8"))

    # --- storage ------------------------------------------------------------
    s3_bucket: str = field(default_factory=lambda: _env("RE_S3_BUCKET", "team20-s3-test-bucket"))
    ddb_index_table: str = field(default_factory=lambda: _env("RE_DDB_INDEX_TABLE", _env("RE_DDB_TABLE", "team20-resource-engine-index")))
    ddb_hsds_table: str = field(default_factory=lambda: _env("RE_DDB_HSDS_TABLE", "re-hsds"))
    ddb_findings_table: str = field(default_factory=lambda: _env("RE_DDB_FINDINGS_TABLE", "re-findings"))
    ddb_tasks_table: str = field(default_factory=lambda: _env("RE_DDB_TASKS_TABLE", "re-tasks"))
    ddb_runs_table: str = field(default_factory=lambda: _env("RE_DDB_RUNS_TABLE", "re-runs"))
    ddb_keys_table: str = field(default_factory=lambda: _env("RE_DDB_KEYS_TABLE", "re-api-keys"))
    kms_key_id: str = field(default_factory=lambda: _env("RE_KMS_KEY_ID"))  # empty -> SSE-S3

    # --- queues -------------------------------------------------------------
    sqs_verify_url: str = field(default_factory=lambda: _env("RE_SQS_VERIFY_URL"))
    sqs_jobs_url: str = field(default_factory=lambda: _env("RE_SQS_JOBS_URL"))

    # --- sources ------------------------------------------------------------
    odrs_host: str = field(default_factory=lambda: _env("ODRS_HOST", "https://rbg.1degree.org:3002"))
    odrs_api_key_secret: str = field(default_factory=lambda: _env("ODRS_API_KEY_SECRET", "re/odrs-api-key"))
    ndp_mode: str = field(default_factory=lambda: _env("NDP_MODE", "sample"))
    ndp_mock_url: str = field(default_factory=lambda: _env("NDP_MOCK_URL"))
    ndp_token_secret: str = field(default_factory=lambda: _env("NDP_TOKEN_SECRET"))

    # --- agents -------------------------------------------------------------
    website_source: str = field(default_factory=lambda: _env("WEBSITE_SOURCE", "sample"))
    agent_mode: str = field(default_factory=lambda: _env("AGENT_MODE", "agentcore"))  # agentcore | inprocess | off
    verifier_agent_arn: str = field(default_factory=lambda: _env("VERIFIER_AGENT_ARN"))
    discovery_agent_arn: str = field(default_factory=lambda: _env("DISCOVERY_AGENT_ARN"))
    gateway_url: str = field(default_factory=lambda: _env("GATEWAY_URL"))
    memory_id: str = field(default_factory=lambda: _env("AGENTCORE_MEMORY_ID"))
    bedrock_model_id: str = field(default_factory=lambda: _env("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"))
    tools_api_key_secret: str = field(default_factory=lambda: _env("TOOLS_API_KEY_SECRET", "re/tools-api-key"))
    tools_api_key: str = field(default_factory=lambda: _env("TOOLS_API_KEY"), repr=False)  # dev only
    api_key_pepper_secret: str = field(default_factory=lambda: _env("API_KEY_PEPPER_SECRET", "re/api-key-pepper"))

    # --- rules / timings ----------------------------------------------------
    stale_days: int = field(default_factory=lambda: _int("STALE_DAYS", 365))
    recheck_days: int = field(default_factory=lambda: _int("RECHECK_DAYS", 30))
    snooze_days: int = field(default_factory=lambda: _int("SNOOZE_DAYS", 3))
    lease_hours: int = field(default_factory=lambda: _int("LEASE_HOURS", 2))
    local_timezone: str = field(default_factory=lambda: _env("RE_LOCAL_TZ", "America/Los_Angeles"))

    # --- discovery ----------------------------------------------------------
    discovery_enabled: bool = field(default_factory=lambda: _bool("DISCOVERY_ENABLED", True))
    discovery_autopublish: str = field(default_factory=lambda: _env("DISCOVERY_AUTOPUBLISH", "after_verifier"))
    discovery_max_creates: int = field(default_factory=lambda: _int("DISCOVERY_MAX_CREATES_PER_RUN", 25))

    # --- auth ---------------------------------------------------------------
    auth_mode: str = field(default_factory=lambda: _env("AUTH_MODE", "cognito"))  # cognito | dev
    cognito_user_pool_id: str = field(default_factory=lambda: _env("COGNITO_USER_POOL_ID"))
    cognito_client_id: str = field(default_factory=lambda: _env("COGNITO_CLIENT_ID"))
    cognito_domain: str = field(default_factory=lambda: _env("COGNITO_DOMAIN"))
    ui_url: str = field(default_factory=lambda: _env("UI_URL", "/ui/"))  # /auth/callback sends the browser here
    # React build served at /ui (frontend/: npm run build). The image sets UI_DIR=/srv/ui.
    ui_dir: str = field(default_factory=lambda: _env("UI_DIR", str(Path(__file__).resolve().parents[2] / "frontend" / "dist")))
    cors_origins: str = field(default_factory=lambda: _env("CORS_ORIGINS"))  # comma list; empty = no CORS
    # Discovery runtime inbound auth (AgentCore Identity): sigv4 | jwt (Cognito client_credentials token)
    discovery_auth: str = field(default_factory=lambda: _env("DISCOVERY_AUTH", "sigv4"))
    discovery_qualifier: str = field(default_factory=lambda: _env("DISCOVERY_QUALIFIER"))  # runtime endpoint, e.g. live
    agent_m2m_client_id: str = field(default_factory=lambda: _env("AGENT_M2M_CLIENT_ID"))
    agent_m2m_secret: str = field(default_factory=lambda: _env("AGENT_M2M_SECRET", "re/agent-m2m-secret"))

    # --- search / metrics ---------------------------------------------------
    opensearch_endpoint: str = field(default_factory=lambda: _env("OPENSEARCH_ENDPOINT"))
    opensearch_index: str = field(default_factory=lambda: _env("OPENSEARCH_INDEX", "re-services"))
    embedding_model_id: str = field(default_factory=lambda: _env("EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0"))
    metrics_namespace: str = field(default_factory=lambda: _env("RE_METRICS_NAMESPACE", "ResourceEngine"))

    @property
    def is_memory(self) -> bool:
        return self.store_backend == "memory"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
