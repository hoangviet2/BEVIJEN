"""AgentCore Gateway Lambda targets: web-tools (fetch_page, check_site, geocode, places_near, pdf_text)
and assist-tools (search_knowledge, translate_text, send_message).

The Gateway invokes the function with the tool arguments as the event and the
tool name in `context.client_context.custom["bedrockAgentCoreToolName"]`
("<target>___<tool>"). Package: this file + agents/tools/web_tools.py
(see infra/deploy.sh `lambda` step). Runtime: Python 3.12.
"""

from __future__ import annotations

import json
import logging

import web_tools

log = logging.getLogger()
log.setLevel(logging.INFO)

ALLOWED = {"fetch_page", "check_site", "geocode", "places_near", "pdf_text",
           "search_knowledge", "translate_text", "send_message"}


def handler(event, context):
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    full = custom.get("bedrockAgentCoreToolName", "")
    tool = full.split("___", 1)[-1] if full else event.pop("tool", "")
    if tool not in ALLOWED:
        return {"error": f"unknown tool {tool!r}"}
    args = {k: v for k, v in (event or {}).items() if k in web_tools.TOOLS[tool].__code__.co_varnames}
    try:
        return web_tools.TOOLS[tool](**args)
    except Exception as exc:  # tool errors go back to the agent, which tries an alternative once
        log.warning("tool_failed tool=%s err=%s", tool, type(exc).__name__)
        return {"error": f"{tool} failed: {type(exc).__name__}", "args": json.dumps(args)[:300]}
