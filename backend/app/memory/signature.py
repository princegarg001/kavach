"""
Scam signature extraction — distills a scam message into a memorable fingerprint.

v2 enhancements:
- Canonical form extraction (strip identifiers, keep pitch pattern)
- Dual embedding: raw message + canonical form
- Cluster ID generation for grouping similar scams
"""
import hashlib
import json
import logging
import re
from typing import Optional

from app import llm
from app.config import settings
from app.llm import get_chat_client as get_client, FAST_MODEL
from app.models import TriageEntities, InvestigatorResult, ScamSignature

logger = logging.getLogger("kavach.signature")


# ── Canonical form patterns (strip identifiers) ───────────────────────────────
PHONE_PATTERN = re.compile(r"(?:\+91[\s-]?)?[6-9]\d{9}")
URL_PATTERN = re.compile(r'https?://[^\s<>"\']+|www\.[^\s<>"\']+')
AMOUNT_PATTERN = re.compile(r"(?:Rs\.?|₹)\s*[\d,]+(?:\.\d{2})?")
ACCOUNT_PATTERN = re.compile(r"(?:A/c|account|a/c)\s*(?:XX|xx|ending)?\s*\d{4,}")
OTP_PATTERN = re.compile(r"\b\d{4,6}\b")
DATE_PATTERN = re.compile(r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}")
REFERENCE_PATTERN = re.compile(r"(?:ref|txn|Ref|Txn|UPI)[:/\s]*\w+")


def _canonicalize(raw_text: str) -> str:
    """
    Strip phone numbers, URLs, amounts, account numbers, OTPs, dates, references.
    What remains is the PITCH PATTERN — the semantic skeleton of the scam.
    """
    text = raw_text
    text = PHONE_PATTERN.sub("[PHONE]", text)
    text = URL_PATTERN.sub("[URL]", text)
    text = AMOUNT_PATTERN.sub("[AMOUNT]", text)
    text = ACCOUNT_PATTERN.sub("[ACCOUNT]", text)
    text = REFERENCE_PATTERN.sub("[REF]", text)
    text = DATE_PATTERN.sub("[DATE]", text)
    # Don't strip OTPs — they're important for categorization

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _generate_cluster_id(semantic_shape: str) -> str:
    """Generate a cluster ID from the semantic shape for grouping."""
    # Simple hash-based clustering — same semantic shape = same cluster
    return hashlib.md5(semantic_shape.lower().encode()).hexdigest()[:12]


SIGNATURE_PROMPT = """You are extracting the semantic shape of a scam message for an immunity database.

The semantic shape describes the PITCH PATTERN of the scam, NOT the specific numbers/URLs/names.
Scammers rotate URLs and phone numbers constantly but reuse the same pitch.

Extract:
1. The urgency type (e.g. "account_suspension", "kyc_expiry", "prize_notification", "electricity_disconnection", "loan_approval", "aadhaar_misuse")
2. The action demanded (e.g. "click_link", "call_number", "share_otp", "download_app", "pay_money")
3. The emotional hook (e.g. "fear", "greed", "urgency", "authority", "curiosity")
4. Brand being impersonated (if any)
5. Communication style (e.g. "formal_english", "hinglish", "hindi", "threatening", "congratulatory")

Return ONLY valid JSON:
{
  "semantic_shape": "<concise description like: urgency + KYC expiry + credential link + bank impersonation>",
  "sender_pattern": "<pattern like: mobile_number | DLT_sender | unknown | shortcode>",
  "domain_fingerprint": "<domain or null>",
  "scam_family": "<brief family name like: kyc_phishing | prize_scam | electricity_threat | pan_fraud>"
}"""


async def extract_signature(
    raw_text: str,
    entities: TriageEntities,
    investigation: Optional[InvestigatorResult],
    member_id: str,
    group_id: str,
) -> ScamSignature:
    """Extract a scam signature with dual embeddings (raw + canonical)."""
    client = get_client()

    # ── Step 1: Get embedding of the raw message ──────────────────────────────
    raw_embedding = await llm.embed(raw_text)

    # ── Step 2: Get embedding of the canonical form ───────────────────────────
    canonical = _canonicalize(raw_text)
    canonical_embedding = []
    if canonical and len(canonical) > 20:
        canonical_embedding = await llm.embed(canonical)

    # ── Step 3: Get semantic shape (Strands agent, else direct JSON) ──────────
    try:
        sig_data: dict = {}
        if (settings.AGENT_RUNTIME or "strands").lower() == "strands":
            try:
                from app.strands_runtime import strands_available, build_agent, run_agent
                if strands_available():
                    agent = build_agent(SIGNATURE_PROMPT, kind="fast", max_tokens=400)
                    txt = await run_agent(agent, f"Scam message:\n{raw_text}")
                    m = re.search(r"\{.*\}", txt or "", re.S)
                    if m:
                        sig_data = json.loads(m.group(0))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"strands signature failed ({exc}); using direct call")
        if not sig_data:
            response = await client.chat.completions.create(
                model=FAST_MODEL,
                messages=[
                    {"role": "system", "content": SIGNATURE_PROMPT},
                    {"role": "user", "content": f"Scam message:\n{raw_text}"},
                ],
                max_tokens=256, temperature=0.1,
                response_format={"type": "json_object"},
            )
            sig_data = json.loads(response.choices[0].message.content)

        semantic_shape = sig_data.get("semantic_shape", "unknown scam pattern")
        sender_pattern = sig_data.get("sender_pattern", entities.sender_id or "unknown")
        domain_fingerprint = sig_data.get("domain_fingerprint") or (
            entities.urls[0] if entities.urls else None
        )
    except Exception as exc:
        logger.warning(f"Signature extraction LLM error: {exc}")
        semantic_shape = "unknown scam pattern"
        sender_pattern = entities.sender_id or "unknown"
        domain_fingerprint = entities.urls[0] if entities.urls else None

    # Stable hash of the original message
    message_hash = hashlib.sha256(raw_text.encode()).hexdigest()

    # Generate cluster ID
    cluster_id = _generate_cluster_id(semantic_shape)

    return ScamSignature(
        group_id=group_id,
        message_hash=message_hash,
        embedding=raw_embedding,
        canonical_embedding=canonical_embedding,
        sender_pattern=sender_pattern,
        domain_fingerprint=domain_fingerprint,
        semantic_shape=semantic_shape,
        cluster_id=cluster_id,
        original_text_snippet=raw_text[:200],
        source_member_id=member_id,
    )
