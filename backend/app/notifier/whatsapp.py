"""
Kavach WhatsApp Notifier v2 — Twilio outbound messages.

Sends escalation messages to Mother or Natu depending on policy decision.
Messages are designed to be actionable on a single WhatsApp screen.

v2 enhancements:
- Daily digest delivery
- Risk score visualization
- Enhanced formatting with emojis
- Delivery tracking
"""
import asyncio
import logging
from datetime import datetime
from twilio.rest import Client

from app.config import settings
from app.models import (
    InboundMessage,
    PolicyDecision,
    TriageResult,
    InvestigatorResult,
    PolicyAction,
    DigestMessage,
)

logger = logging.getLogger("kavach.notifier")

_twilio_client: Client | None = None


def get_twilio() -> Client:
    global _twilio_client
    if _twilio_client is None:
        _twilio_client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    return _twilio_client


def _risk_bar(score: int) -> str:
    """Generate a visual risk bar for WhatsApp."""
    filled = score // 10
    empty = 10 - filled
    if score >= 70:
        return "🔴" * filled + "⚪" * empty
    elif score >= 40:
        return "🟡" * filled + "⚪" * empty
    else:
        return "🟢" * filled + "⚪" * empty


def _format_mother_message(
    msg: InboundMessage,
    triage: TriageResult,
    policy: PolicyDecision,
) -> str:
    """Simple yes/no question for Mother."""
    text_preview = msg.raw_text[:120] + ("..." if len(msg.raw_text) > 120 else "")
    risk = _risk_bar(policy.risk_score)

    return (
        f"🛡️ *Kavach Alert*\n\n"
        f"*From:* {msg.member_name}\n"
        f"*Message:*\n_{text_preview}_\n\n"
        f"*Risk Level:* {risk} ({policy.risk_score}/100)\n\n"
        f"Kavach isn't sure about this message. Is it okay?\n\n"
        f"Reply *YES* if it's fine\n"
        f"Reply *NO* if it looks suspicious\n\n"
        f"_Rule: {policy.rule_matched}_\n"
        f"_Reason: {policy.reason}_"
    )


def _format_natu_escalation(
    msg: InboundMessage,
    triage: TriageResult,
    investigation: InvestigatorResult | None,
    policy: PolicyDecision,
    urgent: bool = False,
) -> str:
    """Detailed escalation card for Natu."""
    text_preview = msg.raw_text[:150] + ("..." if len(msg.raw_text) > 150 else "")
    urgency_header = "🚨 *URGENT — Kavach Alert*" if urgent else "⚠️ *Kavach Alert*"
    risk = _risk_bar(policy.risk_score)

    parts = [
        f"{urgency_header}\n",
        f"*Received by:* {msg.member_name}",
        f"*Source:* {msg.source.upper()}",
        f"*Message:*\n_{text_preview}_\n",
        f"*Risk Level:* {risk} ({policy.risk_score}/100)",
    ]

    if triage.entities.amount:
        parts.append(f"💰 *Amount:* ₹{triage.entities.amount:,.0f}")
    if triage.entities.action_demanded:
        parts.append(f"⚡ *Demand:* {triage.entities.action_demanded.replace('_', ' ').title()}")
    if triage.entities.urls:
        parts.append(f"🔗 *Links:* {', '.join(triage.entities.urls[:2])}")

    if investigation:
        parts.append(f"\n*🔬 Investigation:*")
        parts.append(f"Verdict: *{investigation.verdict.upper()}* ({investigation.confidence:.0%})")
        parts.append(f"Threat Score: {investigation.threat_score}/100")
        if investigation.tools_used:
            parts.append(f"Tools: {', '.join(investigation.tools_used[:5])}")
        # Key signals
        for d in investigation.domain_intel[:1]:
            if d.age_days is not None:
                parts.append(f"Domain age: {d.age_days} days")
            if d.urlhaus_hit:
                parts.append(f"⚠️ URLhaus: MALICIOUS")
            if d.phishtank_hit:
                parts.append(f"⚠️ PhishTank: PHISHING")
            if d.google_safe_browsing_hit:
                parts.append(f"⚠️ Google: UNSAFE")
            if d.typosquat_target:
                parts.append(f"⚠️ Impersonating: {d.typosquat_target}")

    parts.extend([
        f"\n*Kavach verdict:* {policy.reason}\n",
        "Reply *BLOCK* to block this and add to immunity.",
        "Reply *SAFE* if this is legitimate.\n",
    ])

    if policy.chakshu_prefilled:
        parts.append(
            "📋 *Chakshu + 1930 complaint pre-filled.* "
            "Reply *REPORT* to submit."
        )

    if urgent:
        parts.append(
            "\n🔴 *IMMEDIATE ACTION RECOMMENDED*\n"
            "Do not share any OTP or personal information."
        )

    return "\n".join(parts)


def _format_digest(digest: DigestMessage) -> str:
    """Format daily digest message."""
    parts = [
        f"📊 *Kavach Daily Digest*\n",
        f"_{digest.period_start.strftime('%b %d')} — {digest.period_end.strftime('%b %d, %Y')}_\n",
        f"📨 Total messages: *{digest.total_messages}*",
        f"🛡️ Scams blocked: *{digest.scams_blocked}*",
        f"⚡ Immunity hits: *{digest.immunity_hits}*",
        f"📲 Escalations: *{digest.escalations}*",
        f"🤖 Agent actions: *{digest.doer_actions}*",
    ]

    if digest.highlights:
        parts.append("\n*Highlights:*")
        for h in digest.highlights[:5]:
            parts.append(f"• {h}")

    parts.append("\n_Kavach keeps your family safe._")
    return "\n".join(parts)


async def send_escalation(
    msg: InboundMessage,
    policy: PolicyDecision,
    triage: TriageResult,
    investigation: InvestigatorResult | None,
) -> bool:
    """
    Send appropriate WhatsApp notification based on policy decision.
    Returns True on success.
    """
    loop = asyncio.get_event_loop()

    try:
        if policy.action == PolicyAction.ESCALATE_MOTHER:
            body = _format_mother_message(msg, triage, policy)
            to = settings.MOTHER_PHONE
            logger.info(f"📲 Sending mother escalation to {to}")

        elif policy.action == PolicyAction.ESCALATE_NATU:
            body = _format_natu_escalation(msg, triage, investigation, policy, urgent=False)
            to = settings.NATU_PHONE
            logger.info(f"📲 Sending Natu escalation to {to}")

        elif policy.action == PolicyAction.ESCALATE_NATU_URGENT:
            body = _format_natu_escalation(msg, triage, investigation, policy, urgent=True)
            to = settings.NATU_PHONE
            logger.warning(f"🚨 Sending URGENT Natu escalation to {to}")

        else:
            logger.debug(f"Policy action {policy.action} — no notification needed")
            return True

        # Run Twilio call in thread pool (it's synchronous)
        client = get_twilio()
        await loop.run_in_executor(
            None,
            lambda: client.messages.create(
                body=body,
                from_=settings.TWILIO_WHATSAPP_FROM,
                to=to,
            )
        )

        # Correlate the reply we expect back to THIS message.
        try:
            from app.db.escalations import record_pending
            if msg.message_id:
                await record_pending(to, msg.message_id)
        except Exception as exc:
            logger.debug(f"pending escalation not recorded: {exc}")

        logger.info(f"✅ WhatsApp notification sent to {to}")
        return True

    except Exception as exc:
        logger.error(f"WhatsApp send failed: {exc}")
        return False


async def send_digest(digest: DigestMessage, to: str) -> bool:
    """Send daily digest to a specific number."""
    loop = asyncio.get_event_loop()
    try:
        body = _format_digest(digest)
        client = get_twilio()
        await loop.run_in_executor(
            None,
            lambda: client.messages.create(
                body=body,
                from_=settings.TWILIO_WHATSAPP_FROM,
                to=to,
            )
        )
        logger.info(f"📊 Daily digest sent to {to}")
        return True
    except Exception as exc:
        logger.error(f"Digest send failed: {exc}")
        return False
