"""How the server talks to the two agents (AGENT.md).

AGENT_MODE
- agentcore  (default) InvokeAgentRuntime on VERIFIER_AGENT_ARN / DISCOVERY_AGENT_ARN
- inprocess  run the same Strands agent code (backend/agents/) inside this process;
             tools still come from the AgentCore Gateway when GATEWAY_URL is set
- off        no agent calls; the worker records `last_check_category=skipped`

Outputs are checked against the result schemas in AGENT.md §2.5 / §3.5; one
retry with "Return only the JSON object" (§6), then the caller marks
`check_failed`.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Protocol

from ..config import Settings

log = logging.getLogger(__name__)

VERDICTS = ("verified", "discrepancy", "missing_information", "needs_manual", "check_failed", "actively_bad")


class AgentOutputError(ValueError):
    pass


def check_verify_output(out: Any, service_id: str) -> dict:
    if not isinstance(out, dict):
        raise AgentOutputError("verifier output is not a JSON object")
    if out.get("verdict") not in VERDICTS:
        raise AgentOutputError(f"verdict must be one of {VERDICTS}")
    if out.get("service_id") not in (None, service_id):
        raise AgentOutputError("service_id does not match the job")
    out["service_id"] = service_id
    for key in ("facts_checked", "facts_confirmed", "finding_ids", "flag_ids"):
        if not isinstance(out.get(key, []), list):
            raise AgentOutputError(f"{key} must be a list")
    return out


def check_discovery_output(out: Any, run_id: str) -> dict:
    if not isinstance(out, dict):
        raise AgentOutputError("discovery output is not a JSON object")
    for key in ("queries", "created", "rejected"):
        if not isinstance(out.get(key, []), list):
            raise AgentOutputError(f"{key} must be a list")
    out["run_id"] = run_id
    return out


def parse_json_text(text: str) -> Any:
    """Agents are told to return only JSON; tolerate code fences / SSE framing."""
    text = text.strip()
    if text.startswith("data:"):
        chunks = [ln[5:].strip() for ln in text.splitlines() if ln.startswith("data:")]
        text = "".join(json.loads(c) if c.startswith('"') else c for c in chunks)
    if "```" in text:
        text = text.split("```")[1].removeprefix("json").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise AgentOutputError("no JSON object in agent output")
    return json.loads(text[start:end + 1])


class AgentClient(Protocol):
    mode: str

    def verify(self, payload: dict) -> dict | None: ...
    def discover(self, payload: dict) -> dict | None: ...


class OffClient:
    mode = "off"

    def verify(self, payload):
        return None

    def discover(self, payload):
        return None


class AgentCoreClient:
    mode = "agentcore"

    def __init__(self, settings: Settings):
        from .. import aws
        self.settings = settings
        self._c = aws.client("bedrock-agentcore", settings)

    def _invoke(self, arn: str, payload: dict, session_id: str) -> Any:
        if not arn:
            raise AgentOutputError("agent runtime ARN not configured")
        resp = self._c.invoke_agent_runtime(agentRuntimeArn=arn, runtimeSessionId=session_id,
                                            payload=json.dumps(payload).encode(), contentType="application/json",
                                            accept="application/json")
        body = resp.get("response")
        raw = body.read() if hasattr(body, "read") else (b"".join(body) if body is not None else b"")
        out = parse_json_text(raw.decode("utf-8", "replace"))
        if isinstance(out, dict):
            out.setdefault("agent_session_id", resp.get("runtimeSessionId", session_id))
        return out

    @staticmethod
    def _session(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}{uuid.uuid4().hex[:8]}"  # >= 33 chars required

    def verify(self, payload):
        sid = payload["service_id"]
        session = self._session("verify")  # fresh session every check (discovery_check must not share context)
        try:
            return check_verify_output(self._invoke(self.settings.verifier_agent_arn, payload, session), sid)
        except (AgentOutputError, ValueError) as exc:
            log.info("verifier_retry reason=%s", type(exc).__name__)
            retry = {**payload, "instruction": "Return only the JSON object."}
            return check_verify_output(self._invoke(self.settings.verifier_agent_arn, retry, session), sid)

    def discover(self, payload):
        out = self._invoke(self.settings.discovery_agent_arn, payload, self._session("discover"))
        return check_discovery_output(out, payload["run_id"])


class InProcessClient:
    mode = "inprocess"

    def __init__(self, settings: Settings):
        from agents.runtime import run_agent  # backend/agents (needs agents/requirements.txt)
        self._run = run_agent
        self.settings = settings

    def verify(self, payload):
        sid = payload["service_id"]
        try:
            return check_verify_output(self._run("verifier", payload), sid)
        except (AgentOutputError, ValueError):
            return check_verify_output(self._run("verifier", {**payload, "instruction": "Return only the JSON object."}), sid)

    def discover(self, payload):
        return check_discovery_output(self._run("discovery", payload), payload["run_id"])


def build_agent_client(settings: Settings) -> AgentClient:
    if settings.agent_mode == "agentcore":
        return AgentCoreClient(settings)
    if settings.agent_mode == "inprocess":
        return InProcessClient(settings)
    return OffClient()
