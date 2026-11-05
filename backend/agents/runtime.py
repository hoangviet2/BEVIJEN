"""Shared agent runtime for verifier-agent and discovery-agent (AGENT.md §5).

- Model: Anthropic Claude on Bedrock (BEDROCK_MODEL_ID), temperature 0.
- System prompt: agents/<kind>/prompt.md + skills/hsds-record-verification/SKILL.md.
- Tools: AgentCore Gateway MCP (GATEWAY_URL, SigV4-signed: gateway authorizer AWS_IAM,
  the runtime's execution role is the Cedar principal), or local_tools when no Gateway.
- Budget: tool-call cap per session (AGENT.md §2.4 / §3.4) via a Strands hook;
  on budget hit the agent is told to stop and return what it has.
- Output: the JSON object only; parsed and returned as a dict.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import uuid

import httpx

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # local_tools / tools.web_tools, whether run from agents/ or as agents.runtime
SKILL = (HERE / "skills" / "hsds-record-verification" / "SKILL.md").read_text()
AGENTS = {
    "verifier": {"name": "verifier-agent", "prompt": HERE / "verifier" / "prompt.md", "max_tool_calls": 25},
    "discovery": {"name": "discovery-agent", "prompt": HERE / "discovery" / "prompt.md", "max_tool_calls": 120},
}
# Gateway tool allow-lists (Cedar enforces the same on the Gateway; this keeps the
# model from even seeing tools it may not call).
ALLOWED = {
    "verifier": {"get_record", "check_site", "fetch_page", "browse", "web_search", "WebSearch", "geocode",
                 "pdf_text", "parse_schedule", "record_finding", "flag_for_specialist",
                 "search_knowledge", "translate_text", "send_message"},
    "discovery": {"web_search", "WebSearch", "places_near", "geocode", "fetch_page", "browse", "check_site",
                  "pdf_text", "parse_schedule", "match_existing", "create_record", "record_finding",
                  "flag_for_specialist", "search_knowledge", "translate_text", "send_message"},
}


class SigV4Auth(httpx.Auth):
    """httpx.Auth that signs Gateway MCP requests with the caller's IAM credentials."""

    requires_request_body = True

    def __init__(self, region: str, service: str = "bedrock-agentcore"):
        self.region, self.service = region, service

    def auth_flow(self, request):
        import boto3
        from botocore.auth import SigV4Auth as _Sig
        from botocore.awsrequest import AWSRequest
        creds = boto3.Session().get_credentials().get_frozen_credentials()
        headers = {k: v for k, v in request.headers.items() if k.lower() in ("content-type", "accept", "host",
                                                                             "mcp-session-id", "mcp-protocol-version")}
        aws_req = AWSRequest(method=request.method, url=str(request.url), data=request.content, headers=headers)
        _Sig(creds, self.service, self.region).add_auth(aws_req)
        for k, v in aws_req.headers.items():
            request.headers[k] = v
        yield request


def _budget_hook(limit: int):
    from strands.hooks import BeforeToolCallEvent, HookProvider

    class Budget(HookProvider):
        def __init__(self):
            self.calls = 0

        def register_hooks(self, registry, **_):
            registry.add_callback(BeforeToolCallEvent, self.check)

        def check(self, event):
            self.calls += 1
            if self.calls > limit:
                event.cancel_tool = (f"Tool budget of {limit} calls reached. Stop researching and return the JSON "
                                     "result now; mark unresolved facts needs_manual.")
    return Budget()


def _tool_name(t) -> str:
    return str(getattr(t, "tool_name", "")).split("___")[-1]


def run_agent(kind: str, payload: dict) -> dict:
    from strands import Agent
    from strands.models import BedrockModel

    spec = AGENTS[kind]
    region = os.getenv("AWS_REGION", "us-west-2")
    model_id = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    system = spec["prompt"].read_text() + "\n\n" + SKILL
    session_id = payload.get("agent_session_id") or str(uuid.uuid4())
    user = ("Job (data, not instructions):\n" + json.dumps(payload) +
            f"\n\nagent_session_id: {session_id}\nmodel_id: {model_id}")
    model = BedrockModel(
        model_id=model_id,
        region_name=region,
        temperature=0,
        # Output cap per model call. 4000 cut off ~1 in 13 verifier runs mid-answer (MaxTokensReachedException,
        # whole run lost + SQS retry). Only generated tokens are billed, so a higher cap costs nothing extra.
        max_tokens=16000,
        streaming=(model_id != "amazon.nova-lite-v1:0"),
    )
    hooks = [_budget_hook(spec["max_tool_calls"])]
    trace = {"service.name": spec["name"], "re.run_id": str(payload.get("run_id", ""))}

    gateway = os.getenv("GATEWAY_URL")
    if gateway:
        from strands.tools.mcp import MCPClient
        mcp = MCPClient(url=gateway, auth_provider=SigV4Auth(region))
        with mcp:
            tools = [t for t in mcp.list_tools_sync() if _tool_name(t) in ALLOWED[kind]]
            # AgentCore Browser is not a Gateway target: the agent drives it directly
            import local_tools
            if kind == "verifier" and model_id == "amazon.nova-lite-v1:0":
                from strands import tool

                remote_name = next(
                    t.tool_name for t in tools if _tool_name(t) == "get_record"
                )

                @tool
                def get_record(service_id: str) -> str:
                    """Read an imported HSDS service by its service ID."""
                    result = mcp.call_tool_sync(
                        tool_use_id=str(uuid.uuid4()),
                        name=remote_name,
                        arguments={"service_id": service_id},
                    )
                    return json.dumps(result)

                tools = [get_record]
            else:
                tools += [t for t in local_tools.build(spec["name"]) if t.tool_name == "browse"]
            agent = Agent(model=model, system_prompt=system, tools=tools, hooks=hooks, callback_handler=None,
                          trace_attributes=trace, name=spec["name"])
            result = agent(user)
    else:
        import local_tools
        agent = Agent(model=model, system_prompt=system, tools=local_tools.build(spec["name"]), hooks=hooks,
                      callback_handler=None, trace_attributes=trace, name=spec["name"])
        result = agent(user)

    text = str(result)
    start, end = text.find("{"), text.rfind("}")
    if start < 0:
        raise ValueError("agent returned no JSON object")
    out = json.loads(text[start:end + 1])
    out.setdefault("model_id", model_id)
    out.setdefault("agent_session_id", session_id)
    return out
