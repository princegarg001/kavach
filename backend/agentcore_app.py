"""
Kavach on Amazon Bedrock AgentCore Runtime.

This is the entrypoint the AgentCore Runtime invokes. It runs the *heavy* half of
Kavach — the Strands agents, the forensic tools, fastembed, and the Supabase /
Twilio side-effects — inside the managed runtime container. The thin ingress API
(app/main.py with PIPELINE_MODE=agentcore) just does webhooks, persistence, SSE.

Local dev:   python agentcore_app.py            # serves POST /invocations on :8080
Deploy:      agentcore configure --entrypoint agentcore_app.py --name kavach
             agentcore launch
Invoke:      agentcore invoke '{"member_id":"t","member_name":"T","raw_text":"...","source":"sms"}'
"""
from __future__ import annotations

import json
import logging

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from app.models import InboundMessage
from app.agents.graph import run_kavach_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("kavach.agentcore")

app = BedrockAgentCoreApp()


def _jsonable(v):
    return v.model_dump(mode="json") if hasattr(v, "model_dump") else v


@app.entrypoint
async def invoke(payload: dict) -> dict:
    """
    payload = an InboundMessage as a dict:
      {"member_id","member_name","raw_text","source","message_id"?,"received_at"?}
    returns the pipeline result dict (triage / immunity / reputation / investigation
    / doer / policy / education_note / stage_traces), each value JSON-serialisable.
    """
    try:
        msg = InboundMessage(**payload)
    except Exception as exc:
        logger.error(f"bad payload: {exc}")
        return {"error": f"invalid payload: {exc}"}

    logger.info(f"▶️  pipeline for {msg.member_id} ({msg.source})")
    result = await run_kavach_pipeline(msg)
    out = {k: _jsonable(v) for k, v in result.items()}
    logger.info(f"✅ done — action={(out.get('policy') or {}).get('action')}")
    return out


if __name__ == "__main__":
    app.run()
