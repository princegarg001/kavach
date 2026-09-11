"""
Educator Agent — writes a short, plain-language note explaining *why* a message
was a scam (or why it was fine), in the recipient's own language.

This is the "for humans" part: the protected person learns the pattern, so the
next lookalike is caught by them, not just by Kavach. The note rides along on the
audit event and the daily digest — it is never a blocking step.
"""
from __future__ import annotations

import logging

from app.config import settings
from app.llm import get_chat_client as get_client, FAST_MODEL
from app.models import InvestigatorResult, PolicyDecision, TriageResult

logger = logging.getLogger("kavach.educator")


SYSTEM_PROMPT = """You explain scams to non-technical Indian family members (often parents or grandparents).

Write 2 short sentences, warm and simple, no jargon:
1. What this message was and what the scammer wanted.
2. The one tell-tale sign to remember for next time.

Match the language of the original message: reply in English for English, Hindi (Devanagari) for Hindi, and simple Hinglish for Hinglish. Under 45 words. No greeting, no sign-off, just the explanation."""


async def write_education_note(
    raw_text: str,
    triage: TriageResult,
    investigation: InvestigatorResult | None,
    policy: PolicyDecision,
) -> str | None:
    """Return a plain-language explanation, or None if not worth sending one."""
    if not settings.EDUCATION_NOTES_ENABLED:
        return None
    # Only educate on things that were actually risky or blocked.
    action = policy.action.value
    educate = (
        triage.category.value == "scam"
        or action in ("silent_kill", "escalate_natu", "escalate_natu_urgent", "escalate_mother")
        or (investigation is not None and investigation.verdict == "scam")
    )
    if not educate:
        return None

    facts = [f"Original message: {raw_text[:400]}", f"Language: {triage.language}"]
    if triage.urgency_signals:
        facts.append(f"Urgency phrases used: {', '.join(triage.urgency_signals[:4])}")
    if triage.entities.action_demanded:
        facts.append(f"It wanted the reader to: {triage.entities.action_demanded}")
    if investigation and investigation.domain_intel:
        d = investigation.domain_intel[0]
        if d.age_days is not None:
            facts.append(f"Link domain age: {d.age_days} days")
        if d.typosquat_target:
            facts.append(f"Link impersonates: {d.typosquat_target}")
        if any(x.urlhaus_hit or x.phishtank_hit or x.google_safe_browsing_hit for x in investigation.domain_intel):
            facts.append("Link is on public malware/phishing blocklists")
    if investigation and investigation.html_analysis and investigation.html_analysis.credential_harvest_score > 0.3:
        facts.append("The page asked for passwords/OTP/Aadhaar")

    prompt = "\n".join(facts)

    # Strands agent path
    if (settings.AGENT_RUNTIME or "strands").lower() == "strands":
        try:
            from app.strands_runtime import strands_available, build_agent, run_agent
            if strands_available():
                agent = build_agent(SYSTEM_PROMPT, kind="fast", max_tokens=400)
                note = (await run_agent(agent, prompt)).strip()
                return note or None
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"strands educator failed ({exc}); using direct call")

    try:
        resp = await get_client().chat.completions.create(
            model=FAST_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=400,
            temperature=0.3,
        )
        note = (resp.choices[0].message.content or "").strip()
        return note or None
    except Exception as exc:
        logger.warning(f"education note failed: {exc}")
        return None
