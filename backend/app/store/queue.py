"""Job queues: SQS in AWS, a deque in memory.

Two logical queues (design §3):
- `verify` -> `re-verify-jobs`  {service_id, run_id, mode}
- `jobs`   -> `re-jobs`         {type: crawl|recheck|discovery|ingest, ...}
EventBridge Scheduler drops messages on `jobs`; the server's workers consume them.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class Message:
    body: dict
    receipt: str
    receive_count: int = 1


class Queue(Protocol):
    def send(self, body: dict, *, dedupe_id: str | None = None) -> None: ...
    def receive(self, max_messages: int = 10, wait_seconds: int = 0) -> list[Message]: ...
    def ack(self, message: Message) -> None: ...


class MemoryQueue:
    def __init__(self) -> None:
        self._q: deque[dict] = deque()
        self._inflight: dict[str, dict] = {}
        self._lock = threading.Lock()

    def send(self, body, *, dedupe_id=None):
        with self._lock:
            self._q.append(json.loads(json.dumps(body, default=str)))

    def receive(self, max_messages=10, wait_seconds=0):
        out = []
        with self._lock:
            while self._q and len(out) < max_messages:
                body = self._q.popleft()
                receipt = str(uuid.uuid4())
                self._inflight[receipt] = body
                out.append(Message(body, receipt))
        return out

    def ack(self, message):
        with self._lock:
            self._inflight.pop(message.receipt, None)

    def nack(self, message):
        with self._lock:
            body = self._inflight.pop(message.receipt, None)
            if body is not None:
                self._q.append(body)

    def __len__(self) -> int:
        return len(self._q)


class SqsQueue:
    def __init__(self, url: str, sqs_client: Any):
        if not url:
            raise ValueError("queue URL is required (RE_SQS_VERIFY_URL / RE_SQS_JOBS_URL)")
        self.url = url
        self._sqs = sqs_client

    def send(self, body, *, dedupe_id=None):
        kwargs: dict[str, Any] = {"QueueUrl": self.url, "MessageBody": json.dumps(body, default=str)}
        if self.url.endswith(".fifo"):
            kwargs["MessageGroupId"] = body.get("type", "verify")
            kwargs["MessageDeduplicationId"] = dedupe_id or str(uuid.uuid4())
        self._sqs.send_message(**kwargs)

    def receive(self, max_messages=10, wait_seconds=20):
        resp = self._sqs.receive_message(QueueUrl=self.url, MaxNumberOfMessages=min(max_messages, 10),
                                         WaitTimeSeconds=wait_seconds,
                                         MessageSystemAttributeNames=["ApproximateReceiveCount"])
        return [Message(json.loads(m["Body"]), m["ReceiptHandle"],
                        int(m.get("Attributes", {}).get("ApproximateReceiveCount", 1)))
                for m in resp.get("Messages", [])]

    def ack(self, message):
        self._sqs.delete_message(QueueUrl=self.url, ReceiptHandle=message.receipt)

    def nack(self, message):
        # Make it visible again soon; SQS redrive moves it to the DLQ after maxReceiveCount.
        self._sqs.change_message_visibility(QueueUrl=self.url, ReceiptHandle=message.receipt,
                                            VisibilityTimeout=min(60 * message.receive_count, 900))
