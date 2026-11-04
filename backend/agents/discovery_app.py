"""AgentCore Runtime entrypoint: discovery-agent (AGENT.md §3).

    cd backend/agents && agentcore configure -e discovery_app.py -n discovery_agent --region us-west-2
    agentcore launch && agentcore invoke '{"run_id": "dr-…", "zip": "94590", "categories": ["food"], "max_creates": 5}'
"""

from bedrock_agentcore import BedrockAgentCoreApp

from runtime import run_agent

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload, context=None):
    if not isinstance(payload, dict) or not payload.get("run_id") or not (payload.get("zip") or payload.get("county")):
        return {"error": "payload must include run_id and zip or county"}
    session = getattr(context, "session_id", None)
    return run_agent("discovery", {**payload, **({"agent_session_id": session} if session else {})})


if __name__ == "__main__":
    app.run()
