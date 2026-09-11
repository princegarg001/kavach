"""
Provider-agnostic LLM + embedding access.

Chat  : any OpenAI-compatible API — Groq (default), OpenAI, or Azure OpenAI —
        selected by settings.LLM_PROVIDER. Every agent calls get_chat_client()
        and uses FAST_MODEL / SMART_MODEL.
Embed : settings.EMBED_PROVIDER — "fastembed" (local, no key, default),
        "openai", or "jina" (both OpenAI-compatible embedding endpoints).
"""
from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger("kavach.llm")

FAST_MODEL = settings.LLM_FAST_MODEL
SMART_MODEL = settings.LLM_SMART_MODEL

_chat_client = None
_fastembed_model = None
_embed_client = None


def get_chat_client():
    """Singleton AsyncOpenAI / AsyncAzureOpenAI client for chat completions."""
    global _chat_client
    if _chat_client is not None:
        return _chat_client

    provider = (settings.LLM_PROVIDER or "groq").lower()

    if provider == "azure":
        from openai import AsyncAzureOpenAI
        _chat_client = AsyncAzureOpenAI(
            api_key=settings.AZURE_OPENAI_API_KEY,
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
    else:
        from openai import AsyncOpenAI
        if provider == "groq":
            key, base = settings.GROQ_API_KEY, settings.GROQ_BASE_URL
        else:  # "openai" or any custom OpenAI-compatible endpoint
            key, base = settings.OPENAI_API_KEY, settings.OPENAI_BASE_URL
        if not key:
            raise RuntimeError(
                f"LLM_PROVIDER={provider} but no API key set "
                f"({'GROQ_API_KEY' if provider == 'groq' else 'OPENAI_API_KEY'})"
            )
        _chat_client = AsyncOpenAI(api_key=key, base_url=base, max_retries=1, timeout=45.0)

    logger.info(f"🧠 Chat provider={provider} fast={FAST_MODEL} smart={SMART_MODEL}")
    return _chat_client


# Back-compat: modules historically imported get_client from their own file.
get_client = get_chat_client


async def embed(text: str) -> list[float]:
    """Return an embedding vector for `text` (length == settings.EMBED_DIM)."""
    provider = (settings.EMBED_PROVIDER or "fastembed").lower()

    if provider == "fastembed":
        return _embed_fastembed(text)

    # openai / jina — both OpenAI-compatible /embeddings endpoints
    global _embed_client
    if _embed_client is None:
        from openai import AsyncOpenAI
        if provider == "jina":
            _embed_client = AsyncOpenAI(
                api_key=settings.JINA_API_KEY, base_url="https://api.jina.ai/v1"
            )
        elif settings.LLM_PROVIDER.lower() == "azure":
            from openai import AsyncAzureOpenAI
            _embed_client = AsyncAzureOpenAI(
                api_key=settings.AZURE_OPENAI_API_KEY,
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
                api_version=settings.AZURE_OPENAI_API_VERSION,
            )
        else:
            _embed_client = AsyncOpenAI(
                api_key=settings.OPENAI_API_KEY or settings.GROQ_API_KEY,
                base_url=settings.OPENAI_BASE_URL,
            )
    model = "jina-embeddings-v2-base-en" if provider == "jina" else settings.EMBEDDING_MODEL
    resp = await _embed_client.embeddings.create(model=model, input=text)
    return resp.data[0].embedding


def _embed_fastembed(text: str) -> list[float]:
    global _fastembed_model
    if _fastembed_model is None:
        from fastembed import TextEmbedding
        logger.info(f"⬇️  loading local embedding model {settings.EMBED_MODEL_NAME} (first call only)")
        _fastembed_model = TextEmbedding(model_name=settings.EMBED_MODEL_NAME)
    vec = next(iter(_fastembed_model.embed([text])))
    return vec.tolist()
