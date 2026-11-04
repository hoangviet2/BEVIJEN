"""Environment-driven configuration for the HSDS intake service."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Runtime settings, read once from the environment.

    All AWS-facing values default to something sensible for local development.
    Override them with environment variables in any real deployment.
    """

    hsds_version: str = os.getenv("RE_HSDS_VERSION", "3.2.3")
    s3_bucket: str = os.getenv("RE_S3_BUCKET", "team20-s3-test-bucket")
    s3_prefix: str = os.getenv("RE_S3_PREFIX", "hsds")
    ddb_table: str = os.getenv("RE_DDB_TABLE", "team20-resource-engine-index")
    aws_region: str = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-west-2"))

    # Optional custom endpoint for LocalStack / moto ("" means use real AWS).
    aws_endpoint_url: str = os.getenv("RE_AWS_ENDPOINT_URL", "")

    # Namespace for deterministic UUIDv5 generation. Keep this stable: changing
    # it changes every derived id and would break re-ingest upserts.
    id_namespace: str = os.getenv(
        "RE_ID_NAMESPACE", "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
    )


def get_settings() -> Settings:
    """Return a fresh Settings snapshot from the current environment."""
    return Settings()
