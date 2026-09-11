"""
Client for invoking the Kavach pipeline on Bedrock AgentCore Runtime.

Used by app/main.py when PIPELINE_MODE=agentcore: the ingress API forwards each
InboundMessage to the runtime and gets back the same result dict that
run_kavach_pipeline would have produced locally.
"""
from __future__ import annotations

import json
import logging
import uuid

from app.config import settings
from app.models import InboundMessage

logger = logging.getLogger("kavach.runtime_client")

_client = None


def _get_client():
    global _client
    if _client is None:
        import boto3
        _client = boto3.client("bedrock-agentcore", region_name=settings.AGENTCORE_REGION)
    return _client


async def invoke_pipeline_remote(msg: InboundMessage) -> dict:
    """Invoke the AgentCore runtime with one message. Returns the pipeline result dict."""
    if not settings.AGENTCORE_RUNTIME_ARN:
        raise RuntimeError("PIPELINE_MODE=agentcore but AGENTCORE_RUNTIME_ARN is unset")

    import asyncio
    payload = json.dumps(msg.model_dump(mode="json")).encode("utf-8")
    # session id must be >= 33 chars
    session_id = (msg.message_id or uuid.uuid4().hex) + uuid.uuid4().hex

    def _call():
        return _get_client().invoke_agent_runtime(
            agentRuntimeArn=settings.AGENTCORE_RUNTIME_ARN,
            runtimeSessionId=session_id[:128],
            payload=payload,
            contentType="application/json",
            accept="application/json",
        )

    resp = await asyncio.to_thread(_call)

    # The body key differs across SDK versions: "response" (StreamingBody) or "output".
    body = resp.get("response") or resp.get("output") or resp.get("body")
    if hasattr(body, "read"):
        raw = body.read()
    elif isinstance(body, (bytes, bytearray)):
        raw = bytes(body)
    else:
        raw = json.dumps(body).encode() if body is not None else b"{}"

    data = json.loads(raw or b"{}")
    if isinstance(data, dict) and "error" in data and len(data) == 1:
        raise RuntimeError(f"runtime error: {data['error']}")
    return data
