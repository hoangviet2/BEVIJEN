#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
import json
import shlex
import textwrap
from pathlib import Path

source = Path("/tmp/bevijen-test-queued-finding.json")
script = json.loads(source.read_text())["commands"][0]
start = "docker exec -i bevijen-backend python - <<'PY'\n"
code = script.split(start, 1)[1].rsplit("\nPY\n", 1)[0]
head, runner = code.split("messages = queue.receive(1, 5)", 1)
runner = ("messages = queue.receive(1, 5)" + runner).replace(
    'raise SystemExit("No message received. Check the queue before retrying.")',
    "return",
)
worker = head + "\ndef poll_once():\n" + textwrap.indent(runner, "    ") + (
    '\n\nprint("Pacifica demo worker ready", flush=True)\n'
    'while True:\n'
    '    poll_once()\n'
)
compile(worker, "pacifica_demo_worker.py", "exec")
remote = """set -eu
umask 077
name=bevijen-pacifica-demo-worker
if docker container inspect "$name" >/dev/null 2>&1; then
  echo "Worker container already exists."
  docker ps -a --filter "name=^/${name}$"
  exit 1
fi
envfile=$(mktemp)
trap 'rm -f "$envfile"' EXIT
image=$(docker inspect --format '{{.Image}}' bevijen-backend)
docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' bevijen-backend > "$envfile"
"""
remote += ('docker run -d --name "$name" --network host --env-file "$envfile" '
           '"$image" python -u -c ' + shlex.quote(worker) + "\n")
remote += 'docker ps -a --filter "name=^/${name}$"\n'
Path("/tmp/bevijen-start-pacifica-demo-worker.json").write_text(json.dumps({"commands": [remote]}))
PY
aws ssm send-command \
  --region us-west-2 \
  --instance-ids i-07d5f8791f4e1b1d3 \
  --document-name AWS-RunShellScript \
  --parameters file:///tmp/bevijen-start-pacifica-demo-worker.json \
  --query 'Command.CommandId' \
  --output text --no-cli-pager
