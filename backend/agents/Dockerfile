# AgentCore Runtime image for verifier-agent and discovery-agent (linux/arm64).
# AGENT_APP=verifier_app | discovery_app selects the entrypoint (set per runtime by infra/deploy.sh).
FROM --platform=linux/arm64 python:3.12-slim
ENV PYTHONUNBUFFERED=1 AGENT_APP=verifier_app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt aws-opentelemetry-distro \
 && playwright install-deps chromium >/dev/null 2>&1 || true
COPY . .
RUN useradd -r -u 10001 agent && chown -R agent /app
USER agent
EXPOSE 8080
# opentelemetry-instrument sends traces/metrics to AgentCore Observability (CloudWatch)
CMD ["sh", "-c", "opentelemetry-instrument python ${AGENT_APP}.py"]
