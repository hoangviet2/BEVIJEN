# Resource Engine server: API + workers (one image, command picks the role).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY transform /srv/transform
COPY ingestion /srv/ingestion
COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY app /srv/app
COPY gateway /srv/gateway
# Specialist UI: the React build from ../frontend, passed as a named build context
# (infra/deploy.sh and docker-compose.yml do this; build it first with `cd ../frontend && npm run build`).
COPY --from=ui / /srv/ui
ENV UI_DIR=/srv/ui
RUN useradd -r -u 10001 re && chown -R re /srv
USER re
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)"
# Caddy (another container) is the only ingress (SG: 80/443), so trust its X-Forwarded-Proto: without
# it /auth/login built an http:// redirect_uri that Cognito (https callback) rejected.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
