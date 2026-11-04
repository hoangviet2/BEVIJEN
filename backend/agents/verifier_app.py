"""AgentCore Runtime entrypoint: verifier-agent (AGENT.md §2).

    cd backend/agents && agentcore configure -e verifier_app.py -n verifier_agent --region us-west-2
    agentcore launch && agentcore invoke '{"service_id": "<uuid>", "run_id": "manual", "mode": "recheck"}'
"""

from bedrock_agentcore import BedrockAgentCoreApp

from runtime import run_agent

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload, context=None):
    if not isinstance(payload, dict) or not payload.get("service_id"):
        return {"error": "payload must include service_id"}
    session = getattr(context, "session_id", None)
    return run_agent("verifier", {**payload, **({"agent_session_id": session} if session else {})})


if __name__ == "__main__":
    app.run()
