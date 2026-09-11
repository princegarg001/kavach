"""
Kavach Configuration — loads from environment variables.
Copy .env.example to .env and fill in your credentials.
"""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # ── Agent runtime ───────────────────────────────────────────
    # strands = run every agent as a strands.Agent (falls back to `direct`
    #           per-agent on any SDK/import error). direct = hand-rolled loop.
    AGENT_RUNTIME: str = "strands"

    # ── LLM provider ─────────────────────────────────────────────
    # groq | openai | azure  — all OpenAI-compatible chat APIs.
    LLM_PROVIDER: str = "groq"

    # Groq (free, https://console.groq.com/keys)
    GROQ_API_KEY: Optional[str] = None
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"

    # Plain OpenAI (or any OpenAI-compatible endpoint)
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: Optional[str] = None

    # Azure OpenAI (only needed when LLM_PROVIDER=azure)
    AZURE_OPENAI_API_KEY: Optional[str] = None
    AZURE_OPENAI_ENDPOINT: Optional[str] = None
    AZURE_OPENAI_API_VERSION: str = "2024-08-01-preview"

    # Model names. For azure these are *deployment* names; otherwise real model ids.
    # Groq: run `GET /openai/v1/models` to see what your key can use.
    LLM_FAST_MODEL: str = "openai/gpt-oss-20b"          # triage, doer, educator, signature
    LLM_SMART_MODEL: str = "openai/gpt-oss-120b"        # investigator ReAct loop
    # Back-compat aliases (older .env files use these names):
    AZURE_HAIKU_DEPLOYMENT: Optional[str] = None
    AZURE_SONNET_DEPLOYMENT: Optional[str] = None

    # ── Embeddings ──────────────────────────────────────────────
    # fastembed (local, no key) | openai | jina
    EMBED_PROVIDER: str = "fastembed"
    EMBED_MODEL_NAME: str = "BAAI/bge-small-en-v1.5"   # fastembed model id
    EMBED_DIM: int = 384                                # must match the SQL vector(N)
    JINA_API_KEY: Optional[str] = None
    EMBEDDING_MODEL: str = "text-embedding-3-small"     # used when EMBED_PROVIDER=openai

    def model_post_init(self, __context) -> None:  # noqa: D401
        # Let an old-style .env override the new model knobs.
        if self.AZURE_HAIKU_DEPLOYMENT:
            self.LLM_FAST_MODEL = self.AZURE_HAIKU_DEPLOYMENT
        if self.AZURE_SONNET_DEPLOYMENT:
            self.LLM_SMART_MODEL = self.AZURE_SONNET_DEPLOYMENT

    # ── Supabase ─────────────────────────────────────────────────
    SUPABASE_URL: str
    SUPABASE_ANON_KEY: str
    SUPABASE_SERVICE_KEY: str             # for server-side writes

    # ── Twilio (optional — WhatsApp sends are skipped if unset) ───
    TWILIO_ACCOUNT_SID: Optional[str] = None
    TWILIO_AUTH_TOKEN: Optional[str] = None
    TWILIO_WHATSAPP_FROM: str = "whatsapp:+14155238886"  # sandbox number

    # ── Group config (hardcoded for demo) ─────────────────────────
    MOTHER_PHONE: str = "whatsapp:+910000000000"   # e.g. whatsapp:+919876543210
    NATU_PHONE: str = "whatsapp:+910000000001"     # trusted escalation contact
    GROUP_ID: str = "kavach-family-demo"

    # ── Threat Intelligence API Keys (optional) ──────────────────
    GOOGLE_SAFE_BROWSING_KEY: Optional[str] = None
    VIRUSTOTAL_API_KEY: Optional[str] = None

    # ── Immunity Ledger Tuning ────────────────────────────────────
    IMMUNITY_SIMILARITY_THRESHOLD: float = 0.80
    IMMUNITY_DECAY_DAYS: int = 90         # signatures older than this get lower priority
    IMMUNITY_CANONICAL_THRESHOLD: float = 0.84  # higher bar for the pitch-only (canonical) vector

    # ── Investigator Agent ───────────────────────────────────────
    INVESTIGATOR_MODE: str = "agentic"    # agentic | fixed
    INVESTIGATOR_MAX_STEPS: int = 6       # max tool-calling rounds before forcing a verdict
    INVESTIGATOR_EARLY_STOP_SCORE: int = 70  # composite threat score that ends the loop early

    # ── Sender Reputation ────────────────────────────────────────
    REPUTATION_REPEAT_OFFENDER_THRESHOLD: int = 2  # prior confirmed scams => repeat offender

    # Set false on low-memory hosts (e.g. Render free 512MB) — headless Chromium
    # alongside fastembed can OOM there. All other investigator tools still run.
    ENABLE_SCREENSHOT_TOOL: bool = True

    # ── Educator ─────────────────────────────────────────────────
    EDUCATION_NOTES_ENABLED: bool = True

    # ── Digest & Scheduling ───────────────────────────────────────
    DIGEST_HOUR: int = 8                  # 8 AM daily digest
    DIGEST_ENABLED: bool = True

    # ── Email intake (IMAP poll — no OAuth, no phone) ─────────────
    EMAIL_INTAKE_ENABLED: bool = False
    IMAP_HOST: str = "imap.gmail.com"
    IMAP_PORT: int = 993
    IMAP_USER: Optional[str] = None       # the mailbox to watch
    IMAP_PASSWORD: Optional[str] = None   # Gmail: an App Password, not the login password
    IMAP_FOLDER: str = "INBOX"
    EMAIL_POLL_SECONDS: int = 120
    EMAIL_MEMBER_ID: Optional[str] = None  # whose inbox this is; defaults to IMAP_USER

    # ── Rate Limiting ─────────────────────────────────────────────
    RATE_LIMIT_PER_MEMBER: int = 60       # max messages per minute per member

    # ── Ingress / security ───────────────────────────────────────
    # Shared secret the SMS forwarder must send as `X-Kavach-Token`.
    # Empty in dev = no check; REQUIRED when APP_ENV=production.
    INGEST_SECRET: Optional[str] = None
    # Validate Twilio's X-Twilio-Signature on /whatsapp (needs the real public URL).
    TWILIO_VALIDATE_SIGNATURE: bool = False
    DEDUP_WINDOW_SECONDS: int = 600

    # local = run run_kavach_pipeline in-process; agentcore = invoke the AgentCore runtime
    PIPELINE_MODE: str = "local"
    AGENTCORE_RUNTIME_ARN: Optional[str] = None
    AGENTCORE_REGION: str = "us-west-2"

    # ── App ───────────────────────────────────────────────────────
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = "*"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
