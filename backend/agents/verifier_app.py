"""AgentCore Runtime entrypoint: verifier-agent (AGENT.md §2).

    cd backend/agents && agentcore configure -e verifier_app.py -n verifier_agent --region us-west-2
    agentcore launch && agentcore invoke '{"service_id": "<uuid>", "run_id": "manual", "mode": "recheck"}'
"""

from bedrock_agentcore import BedrockAgentCoreApp

from demo_language_review import compare_languages
from runtime import run_agent

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload, context=None):
    if not isinstance(payload, dict) or not payload.get("service_id"):
        return {"error": "payload must include service_id"}
    session = getattr(context, "session_id", None)
    job = {**payload, **({"agent_session_id": session} if session else {})}
    if job.get("mode") == "demo_language_review":
        return compare_languages(job)
    return run_agent("verifier", job)


if __name__ == "__main__":
    app.run()
