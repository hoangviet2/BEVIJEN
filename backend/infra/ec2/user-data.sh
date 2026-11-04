#!/bin/bash
# Resource Engine server bootstrap (Amazon Linux 2023). Placeholders filled by infra/deploy.sh.
set -euxo pipefail
dnf install -y docker amazon-cloudwatch-agent
systemctl enable --now docker
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL "https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-x86_64" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
mkdir -p /opt/re && cd /opt/re
aws s3 cp s3://__BUCKET__/deploy/docker-compose.yml . || true
aws s3 cp s3://__BUCKET__/deploy/.env . || true
cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json <<'CW'
{"logs":{"logs_collected":{"files":{"collect_list":[
  {"file_path":"/var/lib/docker/containers/*/*.log","log_group_name":"/re/server","log_stream_name":"{instance_id}"}]}}}}
CW
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -s \
  -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
