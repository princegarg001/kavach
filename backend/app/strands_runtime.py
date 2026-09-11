"""
Strands Agents SDK runtime for Kavach.

Every reasoning agent (triage, investigator, doer, educator) runs as a
`strands.Agent`. This module owns:
  - the model factory (Groq / OpenAI / Azure, all via the OpenAI-compatible provider)
  - a shared ContextVar so investigator tools can accumulate structured intel
  - trace extraction from `agent.messages` -> list[AgentStep]

Everything is feature-detected and wrapped: if the SDK isn't importable or an
Agent call fails, the caller falls back to the hand-rolled direct path.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any, Optional

from app.config import settings
from app.models import AgentStep

logger = logging.getLogger("kavach.strands")

# Per-request accumulator for investigator tools (set in strands_agents.py).
current_investigation: ContextVar[Any] = ContextVar("current_investigation", default=None)

_models: dict[str, Any] = {}
_available: Optional[bool] = None


def strands_available() -> bool:
    """True if the Strands SDK + OpenAI provider import cleanly."""
    global _available
    if _available is None:
        try:
            import strands  # noqa: F401
            from strands.models.openai import OpenAIModel  # noqa: F401
            _available = True
            logger.info("✅ Strands Agents SDK available")
        except Exception as exc:  # noqa: BLE001
            _available = False
            logger.warning(f"⚠️  Strands SDK unavailable ({exc}); agents use the direct path")
    return _available


def get_model(kind: str = "smart", *, max_tokens: int = 900, temperature: float = 0.1):
    """
    Return a cached Strands model object for the configured provider.
    kind: "smart" (investigator) | "fast" (triage/doer/educator).
    """
    cache_key = f"{kind}:{max_tokens}:{temperature}"
    if cache_key in _models:
        return _models[cache_key]

    from strands.models.openai import OpenAIModel

    model_id = settings.LLM_SMART_MODEL if kind == "smart" else settings.LLM_FAST_MODEL
    provider = (settings.LLM_PROVIDER or "groq").lower()

    if provider == "groq":
        client_args = {"api_key": settings.GROQ_API_KEY, "base_url": settings.GROQ_BASE_URL}
    elif provider == "openai":
        client_args = {"api_key": settings.OPENAI_API_KEY}
        if settings.OPENAI_BASE_URL:
            client_args["base_url"] = settings.OPENAI_BASE_URL
    elif provider == "azure":
        endpoint = (settings.AZURE_OPENAI_ENDPOINT or "").rstrip("/")
        client_args = {
            "api_key": settings.AZURE_OPENAI_API_KEY,
            "base_url": f"{endpoint}/openai/deployments/{model_id}",
            "default_query": {"api-version": settings.AZURE_OPENAI_API_VERSION},
            "default_headers": {"api-key": settings.AZURE_OPENAI_API_KEY},
        }
    else:
        raise RuntimeError(f"unknown LLM_PROVIDER {provider!r}")

    # Don't retry a 400 (bad tool call) with exponential backoff — fail fast to the fallback.
    client_args["max_retries"] = 0

    params = {"temperature": temperature, "max_tokens": max_tokens}
    # gpt-oss / o-series spend a hidden "reasoning" budget from max_tokens; keep it low
    # so short structured answers don't get truncated.
    if "oss" in model_id or model_id.startswith(("o1", "o3", "o4")):
        params["reasoning_effort"] = "low"

    model = OpenAIModel(client_args=client_args, model_id=model_id, params=params)
    _models[cache_key] = model
    return model


def build_agent(system_prompt: str, tools: Optional[list] = None, *, kind: str = "fast",
                max_tokens: int = 900):
    """Construct a strands.Agent with the default stdout callback disabled."""
    from strands import Agent
    return Agent(
        model=get_model(kind, max_tokens=max_tokens),
        tools=tools or [],
        system_prompt=system_prompt,
        callback_handler=None,   # no streaming print to stdout
    )


async def run_agent(agent, prompt: str) -> str:
    """invoke_async if present, else run the sync call in a thread. Returns final text."""
    import asyncio
    invoke_async = getattr(agent, "invoke_async", None)
    if callable(invoke_async):
        result = await invoke_async(prompt)
    else:
        result = await asyncio.to_thread(agent, prompt)
    return _result_text(result)


async def structured(agent, schema, prompt: str):
    """agent.structured_output_async(schema, prompt) with a sync/thread fallback."""
    import asyncio
    fn_async = getattr(agent, "structured_output_async", None)
    if callable(fn_async):
        return await fn_async(schema, prompt)
    fn = getattr(agent, "structured_output", None)
    if callable(fn):
        return await asyncio.to_thread(fn, schema, prompt)
    raise RuntimeError("Strands Agent has no structured_output method")


# ── helpers ──────────────────────────────────────────────────────────────────

def _result_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    msg = getattr(result, "message", None)
    if isinstance(msg, dict):
        return _blocks_text(msg.get("content", []))
    if isinstance(msg, str):
        return msg
    return str(result)


def _blocks_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if isinstance(b, dict) and "text" in b:
            parts.append(b["text"])
        elif isinstance(b, str):
            parts.append(b)
    return " ".join(p.strip() for p in parts if p).strip()


def extract_trace(agent, tool_durations: Optional[dict] = None) -> list[AgentStep]:
    """
    Turn agent.messages into dashboard AgentSteps.
    Handles both Bedrock-style ({"toolUse": {...}}) and OpenAI-ish block shapes.
    """
    tool_durations = tool_durations or {}
    steps: list[AgentStep] = []
    id_to_name: dict[str, str] = {}
    messages = getattr(agent, "messages", None) or []
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "")
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", [])
        if isinstance(content, str):
            content = [{"text": content}]
        for block in content or []:
            if not isinstance(block, dict):
                continue
            if "text" in block and block["text"] and role == "assistant":
                steps.append(AgentStep(kind="thought", content=str(block["text"])[:600]))
            tu = block.get("toolUse") or block.get("tool_use")
            if tu:
                name = tu.get("name", "tool")
                tu_id = tu.get("toolUseId") or tu.get("id") or ""
                if tu_id:
                    id_to_name[tu_id] = name
                steps.append(AgentStep(
                    kind="tool_call", tool_name=name,
                    tool_args=tu.get("input") or tu.get("arguments") or {},
                    duration_ms=int(tool_durations.get(name, 0)),
                ))
            tr = block.get("toolResult") or block.get("tool_result")
            if tr:
                body = tr.get("content", tr)
                if isinstance(body, list):
                    body = _blocks_text(body)
                tr_id = tr.get("toolUseId") or tr.get("id") or ""
                steps.append(AgentStep(
                    kind="observation",
                    tool_name=id_to_name.get(tr_id, ""),
                    content=str(body)[:600],
                ))
    return steps


def count_llm_calls(agent) -> int:
    """Number of assistant turns == number of model round-trips."""
    messages = getattr(agent, "messages", None) or []
    return sum(1 for m in messages if (m.get("role") if isinstance(m, dict) else None) == "assistant")
