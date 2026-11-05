#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
worker_file="$script_dir/pacifica_demo_worker.py"
parameters=$(mktemp)
trap 'rm -f "$parameters"' EXIT

python3 - "$worker_file" "$parameters" <<'PY'
import json
import shlex
import sys
from pathlib import Path

worker = Path(sys.argv[1]).read_text()
compile(worker, "pacifica_demo_worker.py", "exec")
remote = """set -eu
umask 077
name=bevijen-pacifica-demo-worker
if docker container inspect "$name" >/dev/null 2>&1; then
  echo "Worker already exists. Inspect it before replacing it."
  docker ps -a --filter "name=^/${name}$"
  exit 1
fi
envfile=$(mktemp)
trap 'rm -f "$envfile"' EXIT
image=$(docker inspect --format '{{.Image}}' bevijen-backend)
docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' bevijen-backend > "$envfile"
grep -Eq '^RE_SQS_VERIFY_URL=.*/bevijen-pacifica-demo-verify$' "$envfile"
"""
remote += (
    'docker run -d --name "$name" --network host --no-healthcheck '
    '--env-file "$envfile" "$image" python -u -c ' + shlex.quote(worker) + "\n"
)
remote += 'docker ps -a --filter "name=^/${name}$"\n'
Path(sys.argv[2]).write_text(json.dumps({"commands": [remote]}))
PY

aws ssm send-command \
  --region "${AWS_REGION:-us-west-2}" \
  --instance-ids "${BEVIJEN_INSTANCE_ID:-i-07d5f8791f4e1b1d3}" \
  --document-name AWS-RunShellScript \
  --parameters "file://$parameters" \
  --query 'Command.CommandId' --output text --no-cli-pager
