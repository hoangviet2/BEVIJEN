"""One place that builds boto3 clients.

Credentials always come from the standard chain (EC2 instance profile, SSO
profile, env). Nothing here reads static keys. `RE_AWS_ENDPOINT_URL` points the
storage/queue clients at LocalStack when set.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config

from .config import Settings, get_settings

log = logging.getLogger(__name__)

_CFG = Config(retries={"total_max_attempts": 4, "mode": "adaptive"})
# Services LocalStack can emulate; everything else always talks to real AWS.
_LOCAL_OK = {"s3", "dynamodb", "sqs", "secretsmanager", "cloudwatch", "kms"}


@lru_cache(maxsize=None)
def _client(service: str, region: str, endpoint: str) -> Any:
    kwargs: dict[str, Any] = {"region_name": region, "config": _CFG}
    if endpoint and service in _LOCAL_OK:
        kwargs["endpoint_url"] = endpoint
    return boto3.client(service, **kwargs)


def client(service: str, settings: Settings | None = None) -> Any:
    s = settings or get_settings()
    return _client(service, s.aws_region, s.aws_endpoint_url)


@lru_cache(maxsize=None)
def _resource(service: str, region: str, endpoint: str) -> Any:
    kwargs: dict[str, Any] = {"region_name": region, "config": _CFG}
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.resource(service, **kwargs)


def resource(service: str, settings: Settings | None = None) -> Any:
    s = settings or get_settings()
    return _resource(service, s.aws_region, s.aws_endpoint_url)


@lru_cache(maxsize=64)
def secret(secret_id: str, region: str = "", endpoint: str = "") -> str:
    """Read a Secrets Manager string secret (cached for the process lifetime).

    JSON secrets of the form {"value": "..."} or {"api_key": "..."} are unwrapped.
    The value is never logged.
    """
    s = get_settings()
    sm = _client("secretsmanager", region or s.aws_region, endpoint or s.aws_endpoint_url)
    raw = sm.get_secret_value(SecretId=secret_id)["SecretString"]
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            for key in ("value", "api_key", "key", "token"):
                if key in parsed:
                    return str(parsed[key])
    except ValueError:
        pass
    return raw
