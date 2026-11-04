"""Controlled comparison of a stored NDP service and synthetic website evidence."""

import json
import os
from uuid import UUID, uuid4

import boto3
from strands.tools.mcp import MCPClient

from runtime import SigV4Auth

EVIDENCE_URL = "https://bevijen-demo.example/review-language"


def _read_tool(client, tools, name, arguments):
    result = client.call_tool_sync(
        tool_use_id=str(uuid4()), name=tools[name], arguments=arguments
    )
    if result.get("status") != "success" or result.get("isError"):
        raise ValueError(f"{name} did not complete successfully")
    for item in result.get("content", []):
        value = item.get("text")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                continue
        if isinstance(value, dict):
            return value
    raise ValueError(f"{name} did not return a JSON object")


def _validate_comparison(text, current, evidence):
    comparison = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
    proposed = comparison.get("proposed_languages")
    quote = comparison.get("evidence_quote")
    if (
        comparison.get("category") not in {"discrepancy", "category discrepancy"}
        or comparison.get("current_languages") != current
        or not isinstance(proposed, list)
        or not proposed
        or not all(isinstance(language, str) and language.strip() for language in proposed)
        or set(proposed) == set(current)
        or not isinstance(quote, str)
        or not quote.strip()
        or quote not in evidence
        or not all(language.casefold() in quote.casefold() for language in proposed)
    ):
        raise ValueError("Model did not return a supported language discrepancy")
    return proposed, quote


def compare_languages(job):
    service_id = str(UUID(job["service_id"]))
    region = os.getenv("AWS_REGION", "us-west-2")
    model_id = os.environ["BEDROCK_MODEL_ID"]
    with MCPClient(url=os.environ["GATEWAY_URL"], auth_provider=SigV4Auth(region)) as client:
        tools = {tool.tool_name.rsplit("___", 1)[-1]: tool.tool_name
                 for tool in client.list_tools_sync()}
        record = _read_tool(client, tools, "get_record", {"service_id": service_id})
        page = _read_tool(client, tools, "fetch_page", {"url": EVIDENCE_URL})

    service = record["service"]
    evidence = page.get("text", "")
    if service["id"] != service_id or page.get("status") != 200 or not evidence:
        raise ValueError("The requested service or demo evidence is unavailable")
    current = [language["name"] for language in service.get("languages", [])]
    prompt = (
        "Compare the stored languages with the synthetic evidence. "
        "Return one JSON object with category, current_languages, proposed_languages, "
        "and evidence_quote. Set category to discrepancy if the lists differ. "
        "Quote an exact supporting sentence. Use only the supplied data.\n"
        + json.dumps({"current_languages": current, "evidence": evidence})
    )
    response = boto3.client("bedrock-runtime", region_name=region).converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"temperature": 0, "maxTokens": 400},
    )
    content = response["output"]["message"]["content"]
    proposed, quote = _validate_comparison(
        "".join(block.get("text", "") for block in content), current, evidence
    )
    return {
        "synthetic_demo": True,
        "finding": {
            "service_id": service_id,
            "category": "discrepancy",
            "field": "languages",
            "current_value": current,
            "proposed_value": proposed,
            "evidence": [{"url": EVIDENCE_URL, "snippet": quote}],
            "confidence": "medium",
            "model_id": model_id,
            "agent_session_id": job.get("agent_session_id") or "",
        },
    }
